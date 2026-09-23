from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,37}[a-z0-9])?$")
_ORG_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,37}[a-z0-9])?$")
_ORG_ROLES = {"owner", "admin", "member"}
_SESSION_SECONDS = 60 * 60 * 24 * 30
_EMAIL_VERIFICATION_SECONDS = 60 * 30
_EMAIL_RESEND_COOLDOWN_SECONDS = 60


@dataclass
class AccountError(Exception):
    code: str
    message: str
    status: int = 400

    def __str__(self) -> str:
        return self.message


def _now() -> int:
    return int(time.time())


def _normalize_username(value: str) -> str:
    username = value.strip().casefold()
    if not _USERNAME_RE.fullmatch(username):
        raise AccountError(
            "invalid_username",
            "Username must be 3-39 characters using letters, numbers, '_' or '-', and must start and end with a letter or number.",
        )
    return username


def _normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 254 or "@" not in email:
        raise AccountError("invalid_email", "Enter a valid email address.")
    local, _, domain = email.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise AccountError("invalid_email", "Enter a valid email address.")
    return email


def _password_digest(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 10:
        raise AccountError("weak_password", "Password must be at least 10 characters.")
    if len(password) > 1024:
        raise AccountError("invalid_password", "Password is too long.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode("ascii") + "$" + base64.urlsafe_b64encode(digest).decode("ascii")


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountStore:
    """Hosted account/session state. This is intentionally outside the Testamur core package."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_digest TEXT NOT NULL,
                    email_verified_at INTEGER,
                    email_verification_source TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS email_verification_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER,
                    invalidated_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_email_verification_user
                    ON email_verification_tokens(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_email_verification_expires
                    ON email_verification_tokens(expires_at);

                CREATE TABLE IF NOT EXISTS organizations (
                    org_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    created_by TEXT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS organization_memberships (
                    org_id TEXT NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member')),
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(org_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS organization_invitations (
                    invitation_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
                    inviter_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    invitee_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'declined', 'cancelled')),
                    created_at INTEGER NOT NULL,
                    responded_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_org_memberships_user ON organization_memberships(user_id);
                CREATE INDEX IF NOT EXISTS idx_org_invitations_invitee ON organization_invitations(invitee_user_id, status);
                CREATE INDEX IF NOT EXISTS idx_org_invitations_org ON organization_invitations(org_id, status);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
            migrated_legacy = False
            if "email_verified_at" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN email_verified_at INTEGER")
                migrated_legacy = True
            if "email_verification_source" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN email_verification_source TEXT")
                migrated_legacy = True
            if migrated_legacy:
                connection.execute(
                    """
                    UPDATE users
                    SET email_verified_at = COALESCE(email_verified_at, created_at),
                        email_verification_source = COALESCE(email_verification_source, 'legacy_migration')
                    """
                )

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "email": row["email"],
            "display_name": row["display_name"],
            "email_verified": row["email_verified_at"] is not None,
            "email_verified_at": None if row["email_verified_at"] is None else int(row["email_verified_at"]),
            "created_at": int(row["created_at"]),
        }

    @staticmethod
    def _public_profile(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "username": row["username"],
            "display_name": row["display_name"],
            "created_at": int(row["created_at"]),
        }

    def public_user(self, username: str) -> dict[str, Any] | None:
        normalized = username.strip().casefold()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            return None if row is None else self._public_profile(row)

    @staticmethod
    def _organization(row: sqlite3.Row, *, role: str | None = None) -> dict[str, Any]:
        value = {
            "org_id": row["org_id"],
            "slug": row["slug"],
            "display_name": row["display_name"],
            "created_at": int(row["created_at"]),
        }
        if role is not None:
            value["role"] = role
        return value

    def create_organization(self, *, user_id: str, slug: str, display_name: str) -> dict[str, Any]:
        normalized_slug = slug.strip().casefold()
        if not _ORG_SLUG_RE.fullmatch(normalized_slug):
            raise AccountError(
                "invalid_organization_slug",
                "Organization slug must be 3-39 characters using letters, numbers, '_' or '-'.",
            )
        name = display_name.strip()
        if not name or len(name) > 100:
            raise AccountError("invalid_organization_name", "Organization name must be between 1 and 100 characters.")
        org_id = "org_" + secrets.token_hex(12)
        now = _now()
        try:
            with self._connect() as connection:
                if connection.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None:
                    raise AccountError("user_not_found", "Account no longer exists.", 404)
                connection.execute(
                    "INSERT INTO organizations(org_id, slug, display_name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (org_id, normalized_slug, name, user_id, now, now),
                )
                connection.execute(
                    "INSERT INTO organization_memberships(org_id, user_id, role, created_at) VALUES (?, ?, 'owner', ?)",
                    (org_id, user_id, now),
                )
                row = connection.execute("SELECT * FROM organizations WHERE org_id = ?", (org_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            if "slug" in str(exc).lower():
                raise AccountError("organization_slug_taken", "That organization slug is already in use.", 409) from exc
            raise
        assert row is not None
        return self._organization(row, role="owner")

    def list_organizations(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT organizations.*, organization_memberships.role AS member_role
                FROM organization_memberships
                JOIN organizations ON organizations.org_id = organization_memberships.org_id
                WHERE organization_memberships.user_id = ?
                ORDER BY lower(organizations.display_name), organizations.slug
                """,
                (user_id,),
            ).fetchall()
            return [self._organization(row, role=row["member_role"]) for row in rows]

    def owned_organizations(self, *, user_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list_organizations(user_id=user_id) if item.get("role") == "owner"]

    def organization_for_user(self, *, user_id: str, org_ref: str) -> dict[str, Any] | None:
        reference = org_ref.strip().casefold()
        if not reference:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT organizations.*, organization_memberships.role AS member_role
                FROM organizations
                JOIN organization_memberships ON organization_memberships.org_id = organizations.org_id
                WHERE organization_memberships.user_id = ?
                  AND (organizations.org_id = ? OR organizations.slug = ?)
                LIMIT 1
                """,
                (user_id, reference, reference),
            ).fetchone()
            return None if row is None else self._organization(row, role=row["member_role"])

    def workspaces_for_user(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            user = connection.execute("SELECT username, display_name FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if user is None:
            raise AccountError("user_not_found", "Account no longer exists.", 404)
        personal = {
            "workspace_id": "personal",
            "kind": "personal",
            "label": user["display_name"] or user["username"],
            "slug": user["username"],
            "role": "owner",
        }
        organizations = [
            {
                "workspace_id": org["org_id"],
                "kind": "organization",
                "label": org["display_name"],
                "slug": org["slug"],
                "role": org["role"],
            }
            for org in self.list_organizations(user_id=user_id)
        ]
        return [personal, *organizations]

    def resolve_workspace(self, *, user_id: str, workspace_id: str | None) -> dict[str, Any]:
        reference = (workspace_id or "personal").strip()
        if not reference or reference == "personal":
            return next(item for item in self.workspaces_for_user(user_id=user_id) if item["workspace_id"] == "personal")
        org = self.organization_for_user(user_id=user_id, org_ref=reference)
        if org is None:
            raise AccountError("workspace_forbidden", "You do not have access to that workspace.", 403)
        return {
            "workspace_id": org["org_id"],
            "kind": "organization",
            "label": org["display_name"],
            "slug": org["slug"],
            "role": org["role"],
        }

    def list_organization_members(self, *, user_id: str, org_ref: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        org = self.organization_for_user(user_id=user_id, org_ref=org_ref)
        if org is None:
            raise AccountError("organization_not_found", "Organization does not exist or is not accessible.", 404)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT users.username, users.display_name, organization_memberships.user_id,
                       organization_memberships.role, organization_memberships.created_at
                FROM organization_memberships
                JOIN users ON users.user_id = organization_memberships.user_id
                WHERE organization_memberships.org_id = ?
                ORDER BY CASE organization_memberships.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                         lower(users.username)
                """,
                (org["org_id"],),
            ).fetchall()
        members = [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "role": row["role"],
                "joined_at": int(row["created_at"]),
            }
            for row in rows
        ]
        return org, members

    def invite_to_organization(
        self,
        *,
        inviter_user_id: str,
        org_ref: str,
        username: str,
        role: str,
    ) -> dict[str, Any]:
        org = self.organization_for_user(user_id=inviter_user_id, org_ref=org_ref)
        if org is None:
            raise AccountError("organization_not_found", "Organization does not exist or is not accessible.", 404)
        if org["role"] not in {"owner", "admin"}:
            raise AccountError("permission_denied", "Only organization owners and admins can invite members.", 403)
        invite_role = role.strip().casefold()
        if invite_role not in {"admin", "member"}:
            raise AccountError("invalid_role", "Invitation role must be admin or member.")
        target_name = username.strip().casefold()
        now = _now()
        with self._connect() as connection:
            target = connection.execute("SELECT user_id, username, display_name FROM users WHERE username = ?", (target_name,)).fetchone()
            if target is None:
                raise AccountError("user_not_found", "No hosted user exists with that username.", 404)
            if target["user_id"] == inviter_user_id:
                raise AccountError("already_member", "You are already a member of this organization.", 409)
            if connection.execute(
                "SELECT 1 FROM organization_memberships WHERE org_id = ? AND user_id = ?",
                (org["org_id"], target["user_id"]),
            ).fetchone() is not None:
                raise AccountError("already_member", "That user is already a member of this organization.", 409)
            existing = connection.execute(
                """
                SELECT invitation_id FROM organization_invitations
                WHERE org_id = ? AND invitee_user_id = ? AND status = 'pending'
                LIMIT 1
                """,
                (org["org_id"], target["user_id"]),
            ).fetchone()
            if existing is not None:
                raise AccountError("invitation_pending", "That user already has a pending invitation.", 409)
            invitation_id = "inv_" + secrets.token_hex(12)
            connection.execute(
                """
                INSERT INTO organization_invitations(
                    invitation_id, org_id, inviter_user_id, invitee_user_id, role, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (invitation_id, org["org_id"], inviter_user_id, target["user_id"], invite_role, now),
            )
        return {
            "invitation_id": invitation_id,
            "organization": org,
            "invitee": {"username": target["username"], "display_name": target["display_name"]},
            "role": invite_role,
            "status": "pending",
            "created_at": now,
        }

    def list_invitations(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT organization_invitations.*, organizations.slug, organizations.display_name,
                       inviter.username AS inviter_username
                FROM organization_invitations
                JOIN organizations ON organizations.org_id = organization_invitations.org_id
                JOIN users AS inviter ON inviter.user_id = organization_invitations.inviter_user_id
                WHERE organization_invitations.invitee_user_id = ?
                  AND organization_invitations.status = 'pending'
                ORDER BY organization_invitations.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [
            {
                "invitation_id": row["invitation_id"],
                "organization": {
                    "org_id": row["org_id"],
                    "slug": row["slug"],
                    "display_name": row["display_name"],
                },
                "inviter_username": row["inviter_username"],
                "role": row["role"],
                "status": row["status"],
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def respond_to_invitation(self, *, user_id: str, invitation_id: str, accept: bool) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT organization_invitations.*, organizations.slug, organizations.display_name
                FROM organization_invitations
                JOIN organizations ON organizations.org_id = organization_invitations.org_id
                WHERE organization_invitations.invitation_id = ?
                  AND organization_invitations.invitee_user_id = ?
                  AND organization_invitations.status = 'pending'
                """,
                (invitation_id, user_id),
            ).fetchone()
            if row is None:
                raise AccountError("invitation_not_found", "Pending invitation not found.", 404)
            status = "accepted" if accept else "declined"
            if accept:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO organization_memberships(org_id, user_id, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["org_id"], user_id, row["role"], now),
                )
            connection.execute(
                "UPDATE organization_invitations SET status = ?, responded_at = ? WHERE invitation_id = ?",
                (status, now, invitation_id),
            )
            return {
                "invitation_id": invitation_id,
                "organization": {
                    "org_id": row["org_id"],
                    "slug": row["slug"],
                    "display_name": row["display_name"],
                },
                "role": row["role"],
                "status": status,
            }

    def delete_organization(self, *, user_id: str, org_ref: str) -> dict[str, Any]:
        org = self.organization_for_user(user_id=user_id, org_ref=org_ref)
        if org is None:
            raise AccountError("organization_not_found", "Organization does not exist or is not accessible.", 404)
        if org["role"] != "owner":
            raise AccountError("permission_denied", "Only the organization owner can delete it.", 403)
        with self._connect() as connection:
            connection.execute("DELETE FROM organizations WHERE org_id = ?", (org["org_id"],))
        return org

    def _new_session(self, connection: sqlite3.Connection, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        connection.execute(
            "INSERT INTO sessions(session_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (_session_hash(token), user_id, now, now + _SESSION_SECONDS, now),
        )
        return token

    def _new_email_verification(
        self,
        connection: sqlite3.Connection,
        user_id: str,
        *,
        enforce_cooldown: bool = False,
    ) -> str:
        now = _now()
        if enforce_cooldown:
            latest = connection.execute(
                """
                SELECT created_at
                FROM email_verification_tokens
                WHERE user_id = ?
                  AND consumed_at IS NULL
                  AND invalidated_at IS NULL
                  AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, now),
            ).fetchone()
            if latest is not None and now - int(latest["created_at"]) < _EMAIL_RESEND_COOLDOWN_SECONDS:
                retry_after = _EMAIL_RESEND_COOLDOWN_SECONDS - (now - int(latest["created_at"]))
                raise AccountError(
                    "verification_rate_limited",
                    f"Wait {retry_after} seconds before requesting another verification email.",
                    429,
                )
        token = secrets.token_urlsafe(32)
        connection.execute(
            """
            INSERT INTO email_verification_tokens(
                token_hash, user_id, created_at, expires_at, consumed_at, invalidated_at
            ) VALUES (?, ?, ?, ?, NULL, NULL)
            """,
            (_session_hash(token), user_id, now, now + _EMAIL_VERIFICATION_SECONDS),
        )
        return token

    def confirm_email_verification_delivery(self, *, token: str) -> None:
        """Make the delivered token canonical and retire older active links."""
        token_hash = _session_hash(token)
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id
                FROM email_verification_tokens
                WHERE token_hash = ? AND consumed_at IS NULL AND invalidated_at IS NULL
                LIMIT 1
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE email_verification_tokens
                SET invalidated_at = ?
                WHERE user_id = ?
                  AND token_hash <> ?
                  AND consumed_at IS NULL
                  AND invalidated_at IS NULL
                """,
                (now, row["user_id"], token_hash),
            )

    def reject_email_verification_delivery(self, *, token: str) -> None:
        """Retire a token that was never successfully handed to the mail provider."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE email_verification_tokens
                SET invalidated_at = COALESCE(invalidated_at, ?)
                WHERE token_hash = ? AND consumed_at IS NULL
                """,
                (_now(), _session_hash(token)),
            )

    def sign_up(
        self,
        *,
        username: str,
        email: str,
        password: str,
        display_name: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        normalized_username = _normalize_username(username)
        normalized_email = _normalize_email(email)
        name = (display_name or username).strip()
        if not name or len(name) > 80:
            raise AccountError("invalid_display_name", "Display name must be between 1 and 80 characters.")
        digest = _password_digest(password)
        now = _now()
        user_id = "usr_" + secrets.token_hex(12)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users(
                        user_id, username, email, display_name, password_digest,
                        email_verified_at, email_verification_source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (user_id, normalized_username, normalized_email, name, digest, now, now),
                )
                verification_token = self._new_email_verification(connection, user_id)
                row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            text = str(exc).lower()
            if "username" in text:
                raise AccountError("username_taken", "That username is already in use.", 409) from exc
            if "email" in text:
                raise AccountError("email_taken", "That email address is already in use.", 409) from exc
            raise AccountError("account_conflict", "Account already exists.", 409) from exc
        assert row is not None
        return self._public_user(row), verification_token

    def request_email_verification(self, *, identifier: str) -> tuple[dict[str, Any], str] | None:
        normalized = identifier.strip().casefold()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? OR email = ? LIMIT 1",
                (normalized, normalized),
            ).fetchone()
            if row is None or row["email_verified_at"] is not None:
                return None
            token = self._new_email_verification(connection, row["user_id"], enforce_cooldown=True)
            return self._public_user(row), token

    def verify_email(self, *, token: str) -> tuple[dict[str, Any], str]:
        if not isinstance(token, str) or len(token) < 20 or len(token) > 512:
            raise AccountError("invalid_verification_token", "Invalid email verification token.", 400)
        now = _now()
        token_hash = _session_hash(token)
        with self._connect() as connection:
            record = connection.execute(
                """
                SELECT email_verification_tokens.*, users.*
                FROM email_verification_tokens
                JOIN users ON users.user_id = email_verification_tokens.user_id
                WHERE email_verification_tokens.token_hash = ?
                LIMIT 1
                """,
                (token_hash,),
            ).fetchone()
            if record is None:
                raise AccountError("invalid_verification_token", "Invalid email verification token.", 400)
            if record["consumed_at"] is not None or record["invalidated_at"] is not None:
                raise AccountError("verification_token_used", "This verification link is no longer active.", 400)
            if int(record["expires_at"]) <= now:
                connection.execute(
                    "UPDATE email_verification_tokens SET invalidated_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
                raise AccountError("verification_token_expired", "This verification link has expired.", 400)
            user_id = record["user_id"]
            connection.execute(
                """
                UPDATE users
                SET email_verified_at = COALESCE(email_verified_at, ?),
                    email_verification_source = COALESCE(email_verification_source, 'email_link'),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (now, now, user_id),
            )
            connection.execute(
                """
                UPDATE email_verification_tokens
                SET consumed_at = CASE WHEN token_hash = ? THEN ? ELSE consumed_at END,
                    invalidated_at = CASE
                        WHEN token_hash <> ? AND consumed_at IS NULL AND invalidated_at IS NULL THEN ?
                        ELSE invalidated_at
                    END
                WHERE user_id = ?
                """,
                (token_hash, now, token_hash, now, user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            assert row is not None
            session = self._new_session(connection, user_id)
            return self._public_user(row), session

    def sign_in(self, *, identifier: str, password: str) -> tuple[dict[str, Any], str]:
        normalized = identifier.strip().casefold()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? OR email = ? LIMIT 1",
                (normalized, normalized),
            ).fetchone()
            if row is None or not _verify_password(password, row["password_digest"]):
                raise AccountError("invalid_credentials", "Incorrect username/email or password.", 401)
            if row["email_verified_at"] is None:
                raise AccountError(
                    "email_verification_required",
                    "Verify your email address before signing in.",
                    403,
                )
            token = self._new_session(connection, row["user_id"])
            return self._public_user(row), token

    def user_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        now = _now()
        session_hash = _session_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.*, sessions.expires_at
                FROM sessions
                JOIN users ON users.user_id = sessions.user_id
                WHERE sessions.session_hash = ?
                """,
                (session_hash,),
            ).fetchone()
            if row is None:
                return None
            if int(row["expires_at"]) <= now or row["email_verified_at"] is None:
                connection.execute("DELETE FROM sessions WHERE session_hash = ?", (session_hash,))
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_hash = ?",
                (now, session_hash),
            )
            return self._public_user(row)

    def sign_out(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE session_hash = ?", (_session_hash(token),))

    def list_sessions(self, *, user_id: str, current_token: str | None = None) -> list[dict[str, Any]]:
        now = _now()
        current_hash = _session_hash(current_token) if current_token else None
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            rows = connection.execute(
                """
                SELECT session_hash, created_at, expires_at, last_seen_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY last_seen_at DESC, created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [
                {
                    "session_id": row["session_hash"],
                    "created_at": int(row["created_at"]),
                    "expires_at": int(row["expires_at"]),
                    "last_seen_at": int(row["last_seen_at"]),
                    "current": current_hash is not None and hmac.compare_digest(row["session_hash"], current_hash),
                }
                for row in rows
            ]

    def revoke_session(self, *, user_id: str, session_id: str) -> bool:
        if len(session_id) != 64 or any(char not in "0123456789abcdef" for char in session_id):
            raise AccountError("invalid_session", "Invalid session identifier.")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_hash = ? AND user_id = ?",
                (session_id, user_id),
            )
            return cursor.rowcount > 0

    def revoke_other_sessions(self, *, user_id: str, current_token: str | None) -> int:
        if not current_token:
            raise AccountError("authentication_required", "Current session is required.", 401)
        current_hash = _session_hash(current_token)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND session_hash <> ?",
                (user_id, current_hash),
            )
            return int(cursor.rowcount)

    def verify_password(self, *, user_id: str, password: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise AccountError("user_not_found", "Account no longer exists.", 404)
            if not _verify_password(password, row["password_digest"]):
                raise AccountError("invalid_credentials", "Current password is incorrect.", 401)
            return self._public_user(row)

    def change_password(
        self,
        *,
        user_id: str,
        current_password: str,
        new_password: str,
        current_token: str | None,
    ) -> int:
        if not current_token:
            raise AccountError("authentication_required", "Current session is required.", 401)
        new_digest = _password_digest(new_password)
        current_hash = _session_hash(current_token)
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_digest FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise AccountError("user_not_found", "Account no longer exists.", 404)
            if not _verify_password(current_password, row["password_digest"]):
                raise AccountError("invalid_credentials", "Current password is incorrect.", 401)
            connection.execute(
                "UPDATE users SET password_digest = ?, updated_at = ? WHERE user_id = ?",
                (new_digest, now, user_id),
            )
            cursor = connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND session_hash <> ?",
                (user_id, current_hash),
            )
            return int(cursor.rowcount)

    def delete_account(self, *, user_id: str, password: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise AccountError("user_not_found", "Account no longer exists.", 404)
            if not _verify_password(password, row["password_digest"]):
                raise AccountError("invalid_credentials", "Current password is incorrect.", 401)
            public = self._public_user(row)
            connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            return public

    def update_profile(self, *, user_id: str, display_name: str) -> dict[str, Any]:
        name = display_name.strip()
        if not name or len(name) > 80:
            raise AccountError("invalid_display_name", "Display name must be between 1 and 80 characters.")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE user_id = ?",
                (name, now, user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise AccountError("user_not_found", "Account no longer exists.", 404)
            return self._public_user(row)
