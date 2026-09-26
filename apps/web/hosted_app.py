from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from testamur.monitor_provider_manifest import load_monitor_provider_registry
from testamur.monitor_scheduler import MonitorScheduler
from testamur.product_extensions import MonitorTargetProvider, ProductExtensions
from testamur.product_service import TestamurProductService
from testamur.web_app import (
    WEB_ROOT,
    TestamurWebHandler,
    _MAX_JSON_BODY,
    _SECURITY_HEADERS,
    _error,
    _host_allowed,
    _json,
    _static,
    dispatch_api,
    dispatch_api_write,
    dispatch_web_get,
)

from .identity import AccountError, AccountStore
from .mail import MailDeliveryError, ResendMailer

SESSION_COOKIE = "testamur_session"
WORKSPACE_COOKIE = "testamur_workspace"
_MATHHUB_PREFIX = "/sources/mathhub"
_MATHHUB_ROOT = Path(__file__).resolve().parents[2] / "mathhub_app"
_ACCOUNT_ROUTES = {"signin", "signup", "settings"}
_LEGACY_PUBLIC_ROUTE_ALIASES = {
    "/index.html": "/",
    "/learn.html": "/learn",
    "/docs.html": "/docs",
    "/quickstart.html": "/quickstart",
    "/demo.html": "/demo",
    "/plugins.html": "/integrations",
}
_PUBLIC_ASSETS = {
    item.name
    for item in WEB_ROOT.iterdir()
    if item.is_file() and item.suffix.lower() in {".js", ".css"}
} | {"index.html"}
_ADMIN_PREFIX = "/_ops/"
_ADMIN_TOKEN_ENV = "TESTAMUR_ADMIN_PATH_TOKEN"


def _configured_admin_token() -> str | None:
    token = os.environ.get(_ADMIN_TOKEN_ENV, "").strip()
    if not token:
        return None
    if len(token) < 32 or len(token) > 256:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
        return None
    return token


def _admin_path_matches(path: str) -> bool:
    if not path.startswith(_ADMIN_PREFIX):
        return False
    token = _configured_admin_token()
    if token is None:
        return False
    candidate = path[len(_ADMIN_PREFIX):]
    if not candidate or "/" in candidate:
        return False
    return hmac.compare_digest(candidate, token)


def _admin_time(value: int | None) -> str:
    if value is None:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(value)))


def _admin_dashboard_response(store: AccountStore, *, path: str) -> dict[str, Any]:
    snapshot = store.admin_snapshot()
    summary = snapshot["summary"]

    cards = [
        ("Users", summary["total_users"]),
        ("Verified", summary["verified_users"]),
        ("Unverified", summary["unverified_users"]),
        ("Signups · 24h", summary["signups_24h"]),
        ("Signups · 7d", summary["signups_7d"]),
        ("Active users · 24h", summary["active_users_24h"]),
        ("Active users · 7d", summary["active_users_7d"]),
        ("Active sessions", summary["active_sessions"]),
        ("Organizations", summary["organizations"]),
        ("Pending verification", summary["pending_verifications"]),
    ]
    cards_html = "".join(
        (
            '<div class="metric">'
            f'<div class="metric-label">{html.escape(str(label))}</div>'
            f'<div class="metric-value">{int(value)}</div>'
            "</div>"
        )
        for label, value in cards
    )

    rows: list[str] = []
    for user in snapshot["users"]:
        verified = "yes" if user["email_verified"] else "no"
        active = int(user["active_sessions"])
        rows.append(
            "<tr>"
            f'<td><strong>{html.escape(str(user["username"]))}</strong>'
            f'<div class="muted">{html.escape(str(user["display_name"]))}</div></td>'
            f'<td>{html.escape(str(user["email"]))}</td>'
            f'<td><span class="pill {"ok" if user["email_verified"] else "warn"}">{verified}</span></td>'
            f'<td>{html.escape(_admin_time(user["created_at"]))}</td>'
            f'<td>{html.escape(_admin_time(user["last_seen_at"]))}</td>'
            f'<td>{active}</td>'
            f'<td class="mono">{html.escape(str(user["user_id"]))}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7" class="empty">No hosted users yet.</td></tr>')

    refresh_path = html.escape(path, quote=True)
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow,noarchive">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Testamur · Operations</title>
<style>
:root {{ color-scheme: dark; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #0b0d10; color: #eef1f5; }}
main {{ width: min(1500px, calc(100% - 32px)); margin: 32px auto 64px; }}
header {{ display: flex; gap: 24px; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; }}
h1 {{ margin: 0; font-size: 28px; letter-spacing: -0.03em; }}
.sub {{ color: #8f98a5; margin-top: 7px; font-size: 13px; }}
a {{ color: #eef1f5; text-decoration: none; }}
.refresh {{ border: 1px solid #2a3039; border-radius: 10px; padding: 9px 12px; background: #12161b; }}
.metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 24px; }}
.metric {{ border: 1px solid #202630; background: #11151a; border-radius: 12px; padding: 15px; min-height: 92px; }}
.metric-label {{ color: #8f98a5; font-size: 12px; }}
.metric-value {{ font-size: 30px; font-weight: 700; margin-top: 12px; letter-spacing: -0.04em; }}
.panel {{ border: 1px solid #202630; background: #101419; border-radius: 14px; overflow: hidden; }}
.panel-head {{ padding: 15px 17px; border-bottom: 1px solid #202630; display: flex; justify-content: space-between; gap: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid #1b2028; vertical-align: top; }}
th {{ color: #8f98a5; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }}
tr:last-child td {{ border-bottom: 0; }}
.muted {{ color: #7f8996; margin-top: 3px; font-size: 12px; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #9ca6b3; font-size: 11px; }}
.pill {{ display: inline-block; border-radius: 999px; padding: 3px 7px; font-size: 11px; }}
.pill.ok {{ background: #173522; color: #9ae6b4; }}
.pill.warn {{ background: #3a2b12; color: #f6cf7a; }}
.empty {{ color: #8f98a5; padding: 30px; text-align: center; }}
.scroller {{ overflow-x: auto; }}
@media (max-width: 700px) {{ main {{ width: min(100% - 20px, 1500px); margin-top: 20px; }} header {{ align-items: flex-start; flex-direction: column; }} }}
</style>
</head>
<body>
<main>
<header>
  <div>
    <h1>Testamur operations</h1>
    <div class="sub">Private read-only account overview · observed {_admin_time(snapshot["observed_at"])}</div>
  </div>
  <a class="refresh" href="{refresh_path}">Refresh</a>
</header>
<section class="metrics">{cards_html}</section>
<section class="panel">
  <div class="panel-head"><strong>Recent users</strong><span class="muted">newest first · max 200</span></div>
  <div class="scroller">
    <table>
      <thead><tr><th>User</th><th>Email</th><th>Verified</th><th>Created</th><th>Last seen</th><th>Sessions</th><th>ID</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>
</main>
</body>
</html>""".encode("utf-8")

    return {
        "status": int(HTTPStatus.OK),
        "headers": {
            **_SECURITY_HEADERS,
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        },
        "body": body,
    }


def _core_web_cache_token() -> str:
    """Version the actual bundled core Web assets.

    CORE_SNAPSHOT identifies the synced canonical core revision, but testamur-host
    can carry local Web fixes after that snapshot is recorded. Hash the bytes we
    actually serve so every JS/CSS change gets a distinct URL and Safari/CDN
    cannot keep executing a stale dashboard bundle after a host-only deploy.
    """
    digest = hashlib.sha256()
    try:
        for path in sorted(
            (
                *list(WEB_ROOT.glob("*.js")),
                *list(WEB_ROOT.glob("*.css")),
                WEB_ROOT / "index.html",
            ),
            key=lambda item: item.name,
        ):
            if not path.is_file():
                continue
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return "current"
    return digest.hexdigest()[:12]

def _versioned_web_response(response: dict[str, Any]) -> dict[str, Any]:
    """Version local Web assets so a deploy cannot pair a new API with stale JS."""
    if int(response.get("status", 0)) != int(HTTPStatus.OK):
        return response
    headers = dict(response.get("headers") or {})
    content_type = str(headers.get("Content-Type") or "").lower()
    body = bytes(response.get("body") or b"")
    if content_type.startswith("text/html"):
        token = _core_web_cache_token()
        html = body.decode("utf-8")
        html = re.sub(
            r'((?:src|href)=")(/[^"?]+\.(?:js|css))"',
            lambda found: f'{found.group(1)}{found.group(2)}?v={token}"',
            html,
        )
        body = html.encode("utf-8")
        headers["Cache-Control"] = "no-cache, must-revalidate"
    elif (
        "javascript" in content_type
        or content_type.startswith("text/css")
        or content_type.startswith("application/json")
    ):
        headers["Cache-Control"] = "no-cache, must-revalidate"
    return {**response, "headers": headers, "body": body}


def _session_token(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else None


def _workspace_id(cookie_header: str | None) -> str:
    if not cookie_header:
        return "personal"
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return "personal"
    morsel = cookie.get(WORKSPACE_COOKIE)
    return morsel.value.strip() if morsel is not None and morsel.value.strip() else "personal"


def _set_workspace_cookie(workspace_id: str, *, secure: bool) -> str:
    parts = [
        f"{WORKSPACE_COOKIE}={workspace_id}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={60 * 60 * 24 * 365}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _set_cookie(token: str, *, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={60 * 60 * 24 * 30}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _clear_cookie(*, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _mathhub_asset_cache_token() -> str:
    """Version the actual bundled MathHub assets, not only the upstream snapshot.

    testamur-host can carry local MathHub fixes after MATHHUB_SNAPSHOT was
    recorded. Using only that marker leaves Safari with a stable ?v= URL across
    those deploys, so it can execute stale graph code against a new backend.
    """
    digest = hashlib.sha256()
    try:
        for path in sorted(
            (
                *list(_MATHHUB_ROOT.glob("*.js")),
                *list(_MATHHUB_ROOT.glob("*.css")),
                _MATHHUB_ROOT / "index.html",
            ),
            key=lambda item: item.name,
        ):
            if not path.is_file():
                continue
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return "current"
    return digest.hexdigest()[:12]


def _mathhub_static(relative_path: str) -> dict[str, Any]:
    raw = unquote(relative_path or "").lstrip("/")
    if not raw:
        raw = "index.html"
    root = _MATHHUB_ROOT.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        return _error("invalid_path", "invalid MathHub asset path", status=400)
    if not target.is_file():
        return {
            "status": int(HTTPStatus.NOT_FOUND),
            "headers": {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
                **_SECURITY_HEADERS,
            },
            "body": b"Not found\n",
        }
    try:
        body = target.read_bytes()
    except OSError:
        return _error("asset_unavailable", "MathHub asset is unavailable", status=503)
    if target.name == "index.html":
        # Older deployments cached MathHub JS/CSS for five minutes. Revalidation
        # headers on a new deployment cannot invalidate a still-fresh browser
        # entry, so version URLs by the bytes of the bundled MathHub assets.
        cache_token = _mathhub_asset_cache_token()
        html = body.decode("utf-8")
        html = re.sub(
            r'((?:src|href)="\./[^"]+\.(?:js|css))"',
            lambda found: f'{found.group(1)}?v={cache_token}"',
            html,
        )
        body = html.encode("utf-8")
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or target.suffix.lower() in {".js", ".json", ".svg"}:
        content_type += "; charset=utf-8"
    security = {
        **_SECURITY_HEADERS,
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "img-src 'self' data:; style-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'"
        ),
    }
    return {
        "status": int(HTTPStatus.OK),
        "headers": {
            "Content-Type": content_type,
            # MathHub is bundled into the deploy image and changes atomically
            # with the host revision. Revalidate executable/style assets on
            # every navigation so a redeploy cannot leave Safari/Cloudflare
            # running an older UI against a newer API for five minutes.
            "Cache-Control": (
                "no-cache, must-revalidate"
                if target.suffix.lower() in {".html", ".js", ".css", ".json"}
                else "public, max-age=300"
            ),
            **security,
        },
        "body": body,
    }


def _redirect(location: str) -> dict[str, Any]:
    return {
        "status": int(HTTPStatus.SEE_OTHER),
        "headers": {
            "Location": location,
            "Cache-Control": "no-store",
            **_SECURITY_HEADERS,
        },
        "body": b"",
    }


def dispatch_account_get(store: AccountStore, target: str, *, cookie_header: str | None) -> dict[str, Any]:
    path = urlsplit(target).path.rstrip("/") or "/"
    token = _session_token(cookie_header)
    user = store.user_for_session(token)

    if path == "/v1/account/me":
        workspaces: list[dict[str, Any]] = []
        active_workspace = None
        if user is not None:
            workspaces = store.workspaces_for_user(user_id=user["user_id"])
            try:
                active_workspace = store.resolve_workspace(
                    user_id=user["user_id"],
                    workspace_id=_workspace_id(cookie_header),
                )
            except AccountError:
                active_workspace = store.resolve_workspace(user_id=user["user_id"], workspace_id="personal")
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.account.v1",
                "mode": "hosted",
                "authenticated": user is not None,
                "user": user,
                "workspaces": workspaces,
                "active_workspace": active_workspace,
            }
        )

    if path == "/v1/account/sessions":
        if user is None:
            return _error("authentication_required", "Sign in to view sessions.", status=401)
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.sessions.v1",
                "sessions": store.list_sessions(user_id=user["user_id"], current_token=token),
            }
        )

    if path == "/v1/account/organizations":
        if user is None:
            return _error("authentication_required", "Sign in to view organizations.", status=401)
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.organizations.v1",
                "organizations": store.list_organizations(user_id=user["user_id"]),
            }
        )

    if path == "/v1/account/invitations":
        if user is None:
            return _error("authentication_required", "Sign in to view invitations.", status=401)
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.organization-invitations.v1",
                "invitations": store.list_invitations(user_id=user["user_id"]),
            }
        )

    if path.startswith("/v1/account/organizations/"):
        if user is None:
            return _error("authentication_required", "Sign in to view organizations.", status=401)
        org_ref = unquote(path[len("/v1/account/organizations/"):]).strip()
        if "/" in org_ref or not org_ref:
            return _error("route_not_found", "unknown organization route", status=404)
        try:
            organization, members = store.list_organization_members(user_id=user["user_id"], org_ref=org_ref)
        except AccountError as exc:
            return _error(exc.code, exc.message, status=exc.status)
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.organization.v1",
                "organization": organization,
                "members": members,
            }
        )

    if path.startswith("/v1/users/"):
        username = unquote(path[len("/v1/users/"):]).strip()
        profile = store.public_user(username)
        if profile is None:
            return _error("user_not_found", "No hosted user exists with that username.", status=404)
        return _json(
            {
                "ok": True,
                "schema": "testamur.hosted.public-profile.v1",
                "profile": profile,
            }
        )

    return _error("route_not_found", "unknown account route", status=404)


def dispatch_account_write(
    store: AccountStore,
    target: str,
    payload: Mapping[str, Any],
    *,
    cookie_header: str | None,
    secure_cookie: bool = False,
    delete_workspace: Callable[[str], None] | None = None,
    delete_org_workspace: Callable[[str], None] | None = None,
    mailer: ResendMailer | None = None,
    mail_delivery_reporter: Callable[[bool, Exception | None], None] | None = None,
) -> dict[str, Any]:
    path = urlsplit(target).path.rstrip("/") or "/"
    token = _session_token(cookie_header)
    try:
        if path == "/v1/account/signup":
            if mailer is None:
                return _error(
                    "email_delivery_unavailable",
                    "Email verification is not configured on this deployment.",
                    status=503,
                )
            unknown = set(payload) - {"username", "email", "password", "display_name"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown signup fields: {', '.join(sorted(unknown))}")
            username = payload.get("username")
            email = payload.get("email")
            password = payload.get("password")
            display_name = payload.get("display_name")
            if not isinstance(username, str) or not isinstance(email, str) or not isinstance(password, str):
                raise AccountError("invalid_argument", "username, email and password are required strings")
            if display_name is not None and not isinstance(display_name, str):
                raise AccountError("invalid_argument", "display_name must be a string")
            user, verification_token, verification_code = store.sign_up(
                username=username,
                email=email,
                password=password,
                display_name=display_name,
            )
            email_sent = True
            try:
                mailer.send_verification(
                    user=user,
                    token=verification_token,
                    code=verification_code,
                )
                store.confirm_email_verification_delivery(token=verification_token)
                if mail_delivery_reporter is not None:
                    mail_delivery_reporter(True, None)
            except MailDeliveryError as exc:
                store.reject_email_verification_delivery(token=verification_token)
                if mail_delivery_reporter is not None:
                    mail_delivery_reporter(False, exc)
                email_sent = False
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account.v1",
                    "authenticated": False,
                    "verification_required": True,
                    "email_sent": email_sent,
                    "user": user,
                },
                status=202,
            )

        if path == "/v1/account/email-verification/verify":
            unknown = set(payload) - {"token"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown verification fields: {', '.join(sorted(unknown))}")
            verification_token = payload.get("token")
            if not isinstance(verification_token, str):
                raise AccountError("invalid_argument", "token must be a string")
            user, session = store.verify_email(token=verification_token)
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.email-verification.v1",
                    "authenticated": True,
                    "verified": True,
                    "user": user,
                }
            )
            response["headers"]["Set-Cookie"] = _set_cookie(session, secure=secure_cookie)
            return response

        if path == "/v1/account/email-verification/verify-code":
            unknown = set(payload) - {"identifier", "code"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown verification fields: {', '.join(sorted(unknown))}")
            identifier = payload.get("identifier")
            code = payload.get("code")
            if not isinstance(identifier, str) or not isinstance(code, str):
                raise AccountError("invalid_argument", "identifier and code must be strings")
            user, session = store.verify_email_code(identifier=identifier, code=code)
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.email-verification.v1",
                    "authenticated": True,
                    "verified": True,
                    "method": "code",
                    "user": user,
                }
            )
            response["headers"]["Set-Cookie"] = _set_cookie(session, secure=secure_cookie)
            return response

        if path == "/v1/account/email-verification/resend":
            if mailer is None:
                return _error(
                    "email_delivery_unavailable",
                    "Email verification is not configured on this deployment.",
                    status=503,
                )
            unknown = set(payload) - {"identifier"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown resend fields: {', '.join(sorted(unknown))}")
            identifier = payload.get("identifier")
            if not isinstance(identifier, str):
                raise AccountError("invalid_argument", "identifier must be a string")
            try:
                pending = store.request_email_verification(identifier=identifier)
            except AccountError as exc:
                if exc.code != "verification_rate_limited":
                    raise
                pending = None
            if pending is not None:
                pending_user, verification_token, verification_code = pending
                try:
                    mailer.send_verification(
                        user=pending_user,
                        token=verification_token,
                        code=verification_code,
                    )
                    store.confirm_email_verification_delivery(token=verification_token)
                    if mail_delivery_reporter is not None:
                        mail_delivery_reporter(True, None)
                except MailDeliveryError as exc:
                    store.reject_email_verification_delivery(token=verification_token)
                    if mail_delivery_reporter is not None:
                        mail_delivery_reporter(False, exc)
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.email-verification.v1",
                    "accepted": True,
                    "message": "If that account still needs verification, a fresh link has been requested.",
                },
                status=202,
            )

        if path == "/v1/account/signin":
            unknown = set(payload) - {"identifier", "password"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown sign-in fields: {', '.join(sorted(unknown))}")
            identifier = payload.get("identifier")
            password = payload.get("password")
            if not isinstance(identifier, str) or not isinstance(password, str):
                raise AccountError("invalid_argument", "identifier and password are required strings")
            user, session = store.sign_in(identifier=identifier, password=password)
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account.v1",
                    "authenticated": True,
                    "user": user,
                }
            )
            response["headers"]["Set-Cookie"] = _set_cookie(session, secure=secure_cookie)
            return response

        if path == "/v1/account/signout":
            store.sign_out(token)
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account.v1",
                    "authenticated": False,
                    "user": None,
                }
            )
            response["headers"]["Set-Cookie"] = _clear_cookie(secure=secure_cookie)
            return response

        if path == "/v1/account/workspace":
            if user := store.user_for_session(token):
                unknown = set(payload) - {"workspace_id"}
                if unknown:
                    raise AccountError("invalid_argument", f"Unknown workspace fields: {', '.join(sorted(unknown))}")
                workspace_id = payload.get("workspace_id")
                if not isinstance(workspace_id, str):
                    raise AccountError("invalid_argument", "workspace_id must be a string")
                workspace = store.resolve_workspace(user_id=user["user_id"], workspace_id=workspace_id)
                response = _json(
                    {
                        "ok": True,
                        "schema": "testamur.hosted.workspace-selection.v1",
                        "workspace": workspace,
                    }
                )
                response["headers"]["Set-Cookie"] = _set_workspace_cookie(
                    workspace["workspace_id"],
                    secure=secure_cookie,
                )
                return response
            return _error("authentication_required", "Sign in to switch workspaces.", status=401)

        if path == "/v1/account/organizations":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to create an organization.", status=401)
            unknown = set(payload) - {"slug", "display_name"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown organization fields: {', '.join(sorted(unknown))}")
            slug = payload.get("slug")
            display_name = payload.get("display_name")
            if not isinstance(slug, str) or not isinstance(display_name, str):
                raise AccountError("invalid_argument", "slug and display_name must be strings")
            organization = store.create_organization(
                user_id=user["user_id"],
                slug=slug,
                display_name=display_name,
            )
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.organization.v1",
                    "organization": organization,
                }
            )

        if path == "/v1/account/invitations/respond":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to respond to invitations.", status=401)
            unknown = set(payload) - {"invitation_id", "accept"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown invitation fields: {', '.join(sorted(unknown))}")
            invitation_id = payload.get("invitation_id")
            accept = payload.get("accept")
            if not isinstance(invitation_id, str) or not isinstance(accept, bool):
                raise AccountError("invalid_argument", "invitation_id must be a string and accept must be a boolean")
            invitation = store.respond_to_invitation(
                user_id=user["user_id"],
                invitation_id=invitation_id,
                accept=accept,
            )
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.organization-invitation.v1",
                    "invitation": invitation,
                }
            )

        if path.startswith("/v1/account/organizations/") and path.endswith("/invite"):
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to invite organization members.", status=401)
            org_ref = unquote(path[len("/v1/account/organizations/"):-len("/invite")]).strip("/")
            unknown = set(payload) - {"username", "role"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown invitation fields: {', '.join(sorted(unknown))}")
            username = payload.get("username")
            role = payload.get("role", "member")
            if not isinstance(username, str) or not isinstance(role, str):
                raise AccountError("invalid_argument", "username and role must be strings")
            invitation = store.invite_to_organization(
                inviter_user_id=user["user_id"],
                org_ref=org_ref,
                username=username,
                role=role,
            )
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.organization-invitation.v1",
                    "invitation": invitation,
                }
            )

        if path.startswith("/v1/account/organizations/") and path.endswith("/delete"):
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to delete an organization.", status=401)
            org_ref = unquote(path[len("/v1/account/organizations/"):-len("/delete")]).strip("/")
            unknown = set(payload) - {"confirmation"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown organization deletion fields: {', '.join(sorted(unknown))}")
            confirmation = payload.get("confirmation")
            if not isinstance(confirmation, str):
                raise AccountError("invalid_argument", "confirmation must be a string")
            organization = store.organization_for_user(user_id=user["user_id"], org_ref=org_ref)
            if organization is None:
                raise AccountError("organization_not_found", "Organization does not exist or is not accessible.", 404)
            if organization["role"] != "owner":
                raise AccountError("permission_denied", "Only the organization owner can delete it.", 403)
            if confirmation.strip().casefold() != organization["slug"]:
                raise AccountError("confirmation_mismatch", "Type the organization slug exactly to confirm deletion.")
            if delete_org_workspace is None:
                return _error(
                    "organization_deletion_capability_unavailable",
                    "Organization workspace deletion is not configured.",
                    status=503,
                )
            delete_org_workspace(organization["org_id"])
            deleted = store.delete_organization(user_id=user["user_id"], org_ref=organization["org_id"])
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.organization-deletion.v1",
                    "deleted": True,
                    "organization": deleted,
                }
            )
            if _workspace_id(cookie_header) == organization["org_id"]:
                response["headers"]["Set-Cookie"] = _set_workspace_cookie("personal", secure=secure_cookie)
            return response

        if path == "/v1/account/profile":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to update your profile.", status=401)
            unknown = set(payload) - {"display_name"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown profile fields: {', '.join(sorted(unknown))}")
            display_name = payload.get("display_name")
            if not isinstance(display_name, str):
                raise AccountError("invalid_argument", "display_name must be a string")
            updated = store.update_profile(user_id=user["user_id"], display_name=display_name)
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account.v1",
                    "authenticated": True,
                    "user": updated,
                }
            )

        if path == "/v1/account/password":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to change your password.", status=401)
            unknown = set(payload) - {"current_password", "new_password"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown password fields: {', '.join(sorted(unknown))}")
            current_password = payload.get("current_password")
            new_password = payload.get("new_password")
            if not isinstance(current_password, str) or not isinstance(new_password, str):
                raise AccountError("invalid_argument", "current_password and new_password must be strings")
            revoked_count = store.change_password(
                user_id=user["user_id"],
                current_password=current_password,
                new_password=new_password,
                current_token=token,
            )
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account-security.v1",
                    "password_changed": True,
                    "revoked_other_sessions": revoked_count,
                }
            )

        if path == "/v1/account/delete":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to delete your account.", status=401)
            unknown = set(payload) - {"password", "confirmation"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown account deletion fields: {', '.join(sorted(unknown))}")
            password = payload.get("password")
            confirmation = payload.get("confirmation")
            if not isinstance(password, str) or not isinstance(confirmation, str):
                raise AccountError("invalid_argument", "password and confirmation must be strings")
            if confirmation.strip().casefold() != user["username"]:
                raise AccountError("confirmation_mismatch", "Type your username exactly to confirm account deletion.")
            owned = store.owned_organizations(user_id=user["user_id"])
            if owned:
                names = ", ".join(org["slug"] for org in owned[:5])
                raise AccountError(
                    "organization_ownership_requires_cleanup",
                    f"Delete or transfer owned organizations before deleting your account: {names}",
                    409,
                )
            if delete_workspace is None:
                return _error(
                    "account_deletion_capability_unavailable",
                    "Hosted workspace deletion is not configured.",
                    status=503,
                )
            store.verify_password(user_id=user["user_id"], password=password)
            delete_workspace(user["user_id"])
            store.delete_account(user_id=user["user_id"], password=password)
            response = _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.account-deletion.v1",
                    "deleted": True,
                }
            )
            response["headers"]["Set-Cookie"] = _clear_cookie(secure=secure_cookie)
            return response

        if path == "/v1/account/sessions/revoke":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to revoke sessions.", status=401)
            unknown = set(payload) - {"session_id"}
            if unknown:
                raise AccountError("invalid_argument", f"Unknown session fields: {', '.join(sorted(unknown))}")
            session_id = payload.get("session_id")
            if not isinstance(session_id, str):
                raise AccountError("invalid_argument", "session_id must be a string")
            revoked = store.revoke_session(user_id=user["user_id"], session_id=session_id)
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.sessions.v1",
                    "revoked": revoked,
                    "sessions": store.list_sessions(user_id=user["user_id"], current_token=token),
                }
            )

        if path == "/v1/account/sessions/revoke-others":
            user = store.user_for_session(token)
            if user is None:
                return _error("authentication_required", "Sign in to revoke sessions.", status=401)
            if payload:
                raise AccountError("invalid_argument", "revoke-others accepts no fields")
            revoked_count = store.revoke_other_sessions(user_id=user["user_id"], current_token=token)
            return _json(
                {
                    "ok": True,
                    "schema": "testamur.hosted.sessions.v1",
                    "revoked_count": revoked_count,
                    "sessions": store.list_sessions(user_id=user["user_id"], current_token=token),
                }
            )
    except AccountError as exc:
        return _error(exc.code, exc.message, status=exc.status)
    return _error("route_not_found", "unknown account route", status=404)


class HostedTestamurServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        *,
        account_store: AccountStore,
        workspace_root: Path,
        secure_cookie: bool = False,
        mailer: ResendMailer | None = None,
        mail_config_error: str | None = None,
        monitor_target_providers: Mapping[str, MonitorTargetProvider] | None = None,
        monitor_target_provider_specs: Mapping[str, Mapping[str, Any]] | None = None,
        monitor_scheduler_enabled: bool = False,
        monitor_scheduler_poll_seconds: float = 60.0,
    ) -> None:
        super().__init__(address, HostedTestamurWebHandler)
        self.account_store = account_store
        self.workspace_root = workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.secure_cookie = secure_cookie
        self.mailer = mailer
        self.mail_config_error = mail_config_error
        self.mail_last_attempt_at: int | None = None
        self.mail_last_attempt_ok: bool | None = None
        self.mail_last_error_code: str | None = None
        self.mail_last_error_hint: str | None = None
        self.account_last_error_at: int | None = None
        self.account_last_error_type: str | None = None
        self.account_last_error_hint: str | None = None
        self.monitor_target_providers = dict(monitor_target_providers or {})
        self.monitor_target_provider_specs = {
            str(name): dict(spec)
            for name, spec in dict(monitor_target_provider_specs or {}).items()
        }
        self._services: dict[str, TestamurProductService] = {}
        self._services_lock = threading.Lock()
        self.monitor_scheduler = MonitorScheduler(
            self.active_product_services,
            poll_seconds=monitor_scheduler_poll_seconds,
        )
        if monitor_scheduler_enabled:
            self.monitor_scheduler.start()

    def record_mail_delivery(self, ok: bool, error: Exception | None) -> None:
        self.mail_last_attempt_at = int(time.time())
        self.mail_last_attempt_ok = bool(ok)
        if ok:
            self.mail_last_error_code = None
            self.mail_last_error_hint = None
            return
        message = str(error or 'mail delivery failed')
        lower = message.casefold()
        if '(403)' in lower or 'http 403' in lower:
            code = 'provider_rejected_403'
            hint = 'Resend rejected the request. Check the sending domain verification and API key permissions.'
        elif '(401)' in lower or 'http 401' in lower:
            code = 'provider_rejected_401'
            hint = 'Resend rejected the API credentials.'
        elif 'request failed' in lower or 'timed out' in lower or 'timeout' in lower:
            code = 'provider_unreachable'
            hint = 'The host could not complete the request to the mail provider.'
        else:
            code = 'delivery_error'
            hint = 'The mail provider rejected or failed the verification email request.'
        if self.mailer is not None and self.mailer.sender.casefold().endswith('@resend.dev>'):
            hint += ' The configured sender uses the resend.dev testing domain; use a verified Testamur sender domain for production recipients.'
        self.mail_last_error_code = code
        self.mail_last_error_hint = hint
        print(f'[testamur] verification email delivery failed: {message}', file=sys.stderr, flush=True)
    def record_account_failure(self, error: Exception) -> None:
        self.account_last_error_at = int(time.time())
        self.account_last_error_type = type(error).__name__
        self.account_last_error_hint = "An unexpected hosted account error occurred. Check Zeabur logs for the traceback."
        print(
            "[testamur] unexpected account operation failure:\n"
            + "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            file=sys.stderr,
            flush=True,
        )

    def clear_account_failure(self) -> None:
        self.account_last_error_at = None
        self.account_last_error_type = None
        self.account_last_error_hint = None

    def active_product_services(self) -> list[TestamurProductService]:
        with self._services_lock:
            return list(self._services.values())

    def server_close(self) -> None:
        self.monitor_scheduler.stop()
        super().server_close()

    def dashboard_service_for_workspace(
        self, *, user_id: str, workspace_id: str | None
    ) -> TestamurProductService | None:
        """Build a read-only dashboard facade without initializing every store.

        The dashboard projection now reads SQLite directly. Reconstructing the
        complete integrated product service on the first page view needlessly
        initializes every schema/extension and can be expensive on a persistent
        network volume. Existing workspaces can skip that cold-start entirely.
        """
        workspace = self.account_store.resolve_workspace(
            user_id=user_id, workspace_id=workspace_id
        )
        if workspace["kind"] == "personal":
            database = self.workspace_root / user_id / "testamur.sqlite3"
        else:
            database = (
                self.workspace_root
                / "organizations"
                / workspace["workspace_id"]
                / "testamur.sqlite3"
            )
        if not database.is_file():
            return None
        service = object.__new__(TestamurProductService)
        service.database_path = database
        service.extensions = ProductExtensions.empty()
        return service

    def product_service_for_user(self, user_id: str) -> TestamurProductService:
        with self._services_lock:
            existing = self._services.get(user_id)
            if existing is not None:
                return existing
            database = self.workspace_root / user_id / "testamur.sqlite3"
            database.parent.mkdir(parents=True, exist_ok=True)
            service = TestamurProductService.integrated(
                database,
                monitor_target_providers=self.monitor_target_providers,
                monitor_target_provider_specs=self.monitor_target_provider_specs,
            )
            self._services[user_id] = service
            return service

    def product_service_for_workspace(self, *, user_id: str, workspace_id: str | None) -> TestamurProductService:
        workspace = self.account_store.resolve_workspace(user_id=user_id, workspace_id=workspace_id)
        if workspace["kind"] == "personal":
            return self.product_service_for_user(user_id)
        org_id = workspace["workspace_id"]
        cache_key = f"organization:{org_id}"
        with self._services_lock:
            existing = self._services.get(cache_key)
            if existing is not None:
                return existing
            database = self.workspace_root / "organizations" / org_id / "testamur.sqlite3"
            database.parent.mkdir(parents=True, exist_ok=True)
            service = TestamurProductService.integrated(
                database,
                monitor_target_providers=self.monitor_target_providers,
                monitor_target_provider_specs=self.monitor_target_provider_specs,
            )
            self._services[cache_key] = service
            return service

    def delete_organization_workspace(self, org_id: str) -> None:
        if not org_id.startswith("org_") or any(char not in "0123456789abcdef" for char in org_id[4:]) or len(org_id) != 28:
            raise AccountError("invalid_organization_id", "Refusing to delete an invalid organization workspace.", 400)
        root = (self.workspace_root / "organizations").resolve()
        target = (root / org_id).resolve()
        if target.parent != root:
            raise AccountError("invalid_organization_id", "Refusing to delete a workspace outside the hosted root.", 400)
        with self._services_lock:
            self._services.pop(f"organization:{org_id}", None)
        if target.exists():
            shutil.rmtree(target)

    def delete_user_workspace(self, user_id: str) -> None:
        if not user_id.startswith("usr_") or any(char not in "0123456789abcdef" for char in user_id[4:]) or len(user_id) != 28:
            raise AccountError("invalid_user_id", "Refusing to delete an invalid workspace identity.", 400)
        root = self.workspace_root.resolve()
        target = (root / user_id).resolve()
        if target.parent != root:
            raise AccountError("invalid_user_id", "Refusing to delete a workspace outside the hosted root.", 400)
        with self._services_lock:
            self._services.pop(user_id, None)
        if target.exists():
            shutil.rmtree(target)


class HostedTestamurWebHandler(TestamurWebHandler):
    server_version = "TestamurHostedWeb/1"

    def _proxy_mathhub(self) -> dict[str, Any]:
        split = urlsplit(self.path)
        if split.path == _MATHHUB_PREFIX or split.path.startswith(_MATHHUB_PREFIX + "/"):
            upstream_path = split.path[len(_MATHHUB_PREFIX):] or "/"
        else:
            upstream_path = split.path or "/"
        target = "http://127.0.0.1:8001" + upstream_path
        if split.query:
            target += "?" + split.query

        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return _error("invalid_content_length", "invalid Content-Length", status=400)
            if length > 0:
                body = self.rfile.read(length)

        headers = {
            "Accept": self.headers.get("Accept", "*/*"),
            "Accept-Encoding": "identity",
            "User-Agent": self.headers.get("User-Agent", "Testamur-MathHub-Gateway/1"),
        }
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type

        request = urllib.request.Request(target, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = int(getattr(response, "status", response.getcode()))
                payload = response.read()
                upstream_headers = response.headers
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            payload = exc.read()
            upstream_headers = exc.headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _error(
                "mathhub_unavailable",
                f"MathHub upstream is unavailable: {getattr(exc, 'reason', exc)}",
                status=502,
            )

        response_headers = {
            "Cache-Control": upstream_headers.get("Cache-Control", "no-cache"),
            "Content-Type": upstream_headers.get("Content-Type", "application/octet-stream"),
            **_SECURITY_HEADERS,
        }
        for key in ("ETag", "Last-Modified"):
            value = upstream_headers.get(key)
            if value:
                response_headers[key] = value
        return {"status": status, "headers": response_headers, "body": payload}

    @property
    def hosted_server(self) -> HostedTestamurServer:
        return self.server  # type: ignore[return-value]

    def _current_user(self) -> dict[str, Any] | None:
        return self.hosted_server.account_store.user_for_session(_session_token(self.headers.get("Cookie")))

    def _read_json(self) -> Mapping[str, Any] | dict[str, Any]:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise AccountError("invalid_content_type", "write requests require application/json", 415)
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise AccountError("invalid_content_length", "invalid Content-Length", 400) from exc
        if length < 1 or length > _MAX_JSON_BODY:
            raise AccountError(
                "invalid_body_size",
                f"JSON body must be between 1 and {_MAX_JSON_BODY} bytes",
                413,
            )
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountError("invalid_json", "request body must contain valid UTF-8 JSON", 400) from exc
        if not isinstance(payload, Mapping):
            raise AccountError("invalid_json", "request JSON body must be an object", 400)
        return payload

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        path = unquote(split.path or "/")
        name = path.lstrip("/")
        legacy_target = _LEGACY_PUBLIC_ROUTE_ALIASES.get(path)
        if legacy_target is not None:
            self._send(_redirect(legacy_target))
            return
        if path.startswith(_ADMIN_PREFIX):
            if not _admin_path_matches(path):
                self._send(_error("not_found", "Not found.", status=404))
                return
            self._send(_admin_dashboard_response(self.hosted_server.account_store, path=path))
            return
        if path == "/healthz":
            mathhub_ok = False
            mathhub_error = None
            mathhub_runtime: dict[str, Any] = {}
            mathhub_library: dict[str, Any] = {}
            try:
                with urllib.request.urlopen("http://127.0.0.1:8001/api/runtime", timeout=1.5) as response:
                    mathhub_ok = 200 <= int(getattr(response, "status", response.getcode())) < 500
                    raw_runtime = response.read(128 * 1024)
                    decoded_runtime = json.loads(raw_runtime.decode("utf-8")) if raw_runtime else {}
                    if isinstance(decoded_runtime, dict):
                        mathhub_runtime = decoded_runtime
            except Exception as exc:
                mathhub_error = str(getattr(exc, "reason", exc))
            if mathhub_ok:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8001/api/library/status", timeout=1.5) as response:
                        raw_library = response.read(128 * 1024)
                        decoded_library = json.loads(raw_library.decode("utf-8")) if raw_library else {}
                        if isinstance(decoded_library, dict):
                            mathhub_library = decoded_library
                except Exception as exc:
                    mathhub_library = {
                        "ok": False,
                        "error": str(getattr(exc, "reason", exc)),
                    }
            mathhub_last_log = None
            if not mathhub_ok:
                try:
                    log_path = Path("/data/mathhub/startup.log")
                    if log_path.is_file():
                        lines = [line.strip() for line in log_path.read_text(errors="replace").splitlines() if line.strip()]
                        if lines:
                            mathhub_last_log = lines[-1][-600:]
                except OSError:
                    pass
            data_root = Path("/data")
            accounts_db = self.hosted_server.account_store.path
            marker_path = data_root / "testamur" / ".volume-id"
            try:
                volume_marker = marker_path.read_text(encoding="utf-8").strip() if marker_path.is_file() else None
            except OSError:
                volume_marker = None
            try:
                accounts_db_size = accounts_db.stat().st_size if accounts_db.is_file() else 0
            except OSError:
                accounts_db_size = None
            self._send(_json({
                "ok": True,
                "service": "testamur-host",
                "storage": {
                    "data_root": str(data_root),
                    "persistent_volume_mounted": os.path.ismount(data_root),
                    "volume_marker": volume_marker,
                    "accounts_db": str(accounts_db),
                    "accounts_db_exists": accounts_db.is_file(),
                    "accounts_db_size_bytes": accounts_db_size,
                },
                "account_operations": {
                    "last_error_at": self.hosted_server.account_last_error_at,
                    "last_error_type": self.hosted_server.account_last_error_type,
                    "last_error_hint": self.hosted_server.account_last_error_hint,
                },
                "email_delivery": {
                    "provider": "resend" if self.hosted_server.mailer is not None else None,
                    "configured": self.hosted_server.mailer is not None,
                    "configuration_error": self.hosted_server.mail_config_error,
                    "sender_domain": (
                        self.hosted_server.mailer.sender.rsplit("@", 1)[-1].rstrip(">").casefold()
                        if self.hosted_server.mailer is not None and "@" in self.hosted_server.mailer.sender
                        else None
                    ),
                    "last_attempt_at": self.hosted_server.mail_last_attempt_at,
                    "last_attempt_ok": self.hosted_server.mail_last_attempt_ok,
                    "last_error_code": self.hosted_server.mail_last_error_code,
                    "last_error_hint": self.hosted_server.mail_last_error_hint,
                },
                "mathhub": {
                    "ok": mathhub_ok,
                    "ready": bool(mathhub_runtime.get("ready")),
                    "message": mathhub_runtime.get("message"),
                    "setup_running": bool(mathhub_runtime.get("setup_running")),
                    "setup_stage": mathhub_runtime.get("setup_stage"),
                    "setup_step": mathhub_runtime.get("setup_step"),
                    "setup_steps": mathhub_runtime.get("setup_steps"),
                    "setup_error": mathhub_runtime.get("setup_error"),
                    "lean_version": mathhub_runtime.get("lean_version"),
                    "mathlib_revision": mathhub_runtime.get("mathlib_revision"),
                    "library": mathhub_library,
                    "snapshot": (
                        (Path(__file__).resolve().parents[2] / "MATHHUB_SNAPSHOT").read_text(
                            encoding="utf-8"
                        ).strip()
                        if (Path(__file__).resolve().parents[2] / "MATHHUB_SNAPSHOT").is_file()
                        else None
                    ),
                    "error": mathhub_error,
                    "last_log": mathhub_last_log,
                },
            }))
            return
        if path == "/scources/mathhub" or path.startswith("/scources/mathhub/"):
            suffix = path[len("/scources/mathhub"):]
            self._send(_redirect(_MATHHUB_PREFIX + (suffix or "/")))
            return
        if path == _MATHHUB_PREFIX:
            self._send(_redirect(_MATHHUB_PREFIX + "/"))
            return
        if path.startswith(_MATHHUB_PREFIX + "/"):
            relative = path[len(_MATHHUB_PREFIX) + 1:]
            self._send(_mathhub_static(relative))
            return
        if path.startswith("/api/"):
            self._send(self._proxy_mathhub())
            return
        if path.startswith("/v1/account/") or path.startswith("/v1/users/"):
            self._send(
                dispatch_account_get(
                    self.hosted_server.account_store,
                    self.path,
                    cookie_header=self.headers.get("Cookie"),
                )
            )
            return
        if path in {"/signin", "/signup", "/verify-email", "/verify-email-pending", "/settings/profile", "/settings/security", "/settings/organizations"} or path.startswith("/users/") or path.startswith("/organizations/"):
            self._send(_versioned_web_response(_static(WEB_ROOT / "index.html", cache="no-cache")))
            return
        if name in _PUBLIC_ASSETS:
            self._send(_versioned_web_response(_static(WEB_ROOT / name, cache="no-cache")))
            return

        user = self._current_user()
        if path == "/" and user is None:
            self._send(_redirect("/signin"))
            return
        if path == "/v1" or path.startswith("/v1/"):
            if user is None:
                self._send(_error("authentication_required", "Sign in to use hosted Testamur.", status=401))
                return
            workspace_id = _workspace_id(self.headers.get("Cookie"))
            if path == "/v1/dashboard":
                try:
                    dashboard_service = self.hosted_server.dashboard_service_for_workspace(
                        user_id=user["user_id"],
                        workspace_id=workspace_id,
                    )
                    if dashboard_service is not None:
                        self._send(dispatch_api(dashboard_service, self.path))
                        return
                except (AccountError, sqlite3.Error):
                    # Fall back to the full service path. This covers a newly
                    # created or partially migrated workspace without weakening
                    # normal product initialization semantics.
                    pass
            try:
                service = self.hosted_server.product_service_for_workspace(
                    user_id=user["user_id"],
                    workspace_id=workspace_id,
                )
            except AccountError:
                service = self.hosted_server.product_service_for_user(user["user_id"])
            self._send(dispatch_api(service, self.path))
            return
        if user is None:
            self._send(_redirect("/signin"))
            return
        try:
            service = self.hosted_server.product_service_for_workspace(
                user_id=user["user_id"],
                workspace_id=_workspace_id(self.headers.get("Cookie")),
            )
        except AccountError:
            service = self.hosted_server.product_service_for_user(user["user_id"])
        self._send(_versioned_web_response(dispatch_web_get(service, self.path)))

    def do_POST(self) -> None:
        # Hosted deployments sit behind Zeabur/Cloudflare reverse proxies. Do not
        # reject writes based on the forwarded public Host header here; account
        # and workspace authorization below remains authoritative.
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/"):
            self._send(self._proxy_mathhub())
            return

        try:
            payload = self._read_json()
        except AccountError as exc:
            self._send(_error(exc.code, exc.message, status=exc.status))
            return

        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/v1/account/"):
            try:
                response = dispatch_account_write(
                    self.hosted_server.account_store,
                    self.path,
                    payload,
                    cookie_header=self.headers.get("Cookie"),
                    secure_cookie=self.hosted_server.secure_cookie,
                    delete_workspace=self.hosted_server.delete_user_workspace,
                    delete_org_workspace=self.hosted_server.delete_organization_workspace,
                    mailer=self.hosted_server.mailer,
                    mail_delivery_reporter=self.hosted_server.record_mail_delivery,
                )
                self.hosted_server.clear_account_failure()
            except Exception as exc:
                self.hosted_server.record_account_failure(exc)
                response = _error(
                    "account_operation_failed",
                    "Hosted account operation failed unexpectedly. Please retry.",
                    status=500,
                )
            self._send(response)
            return

        user = self._current_user()
        if user is None:
            self._send(_error("authentication_required", "Sign in to modify hosted Testamur.", status=401))
            return
        try:
            service = self.hosted_server.product_service_for_workspace(
                user_id=user["user_id"],
                workspace_id=_workspace_id(self.headers.get("Cookie")),
            )
        except AccountError:
            service = self.hosted_server.product_service_for_user(user["user_id"])
        self._send(dispatch_api_write(service, self.path, payload))

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("TESTAMUR_WEB_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(format, *args)


def build_hosted_server(
    *,
    data_dir: str | Path,
    host: str = "127.0.0.1",
    port: int = 8788,
    secure_cookie: bool = False,
    mailer: ResendMailer | None = None,
    monitor_target_providers: Mapping[str, MonitorTargetProvider] | None = None,
    monitor_target_provider_specs: Mapping[str, Mapping[str, Any]] | None = None,
    monitor_scheduler_enabled: bool = False,
    monitor_scheduler_poll_seconds: float = 60.0,
) -> HostedTestamurServer:
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    registry = None
    if monitor_target_providers is None and monitor_target_provider_specs is None:
        registry = load_monitor_provider_registry()
        monitor_target_providers = registry.providers
        monitor_target_provider_specs = registry.specs
    mail_config_error = None
    if mailer is None:
        try:
            mailer = ResendMailer.from_env()
        except RuntimeError as exc:
            mail_config_error = str(exc)
            mailer = None
    return HostedTestamurServer(
        (host, int(port)),
        account_store=AccountStore(root / "accounts.sqlite3"),
        workspace_root=root / "workspaces",
        secure_cookie=secure_cookie,
        mailer=mailer,
        mail_config_error=mail_config_error,
        monitor_target_providers=monitor_target_providers,
        monitor_target_provider_specs=monitor_target_provider_specs,
        monitor_scheduler_enabled=monitor_scheduler_enabled,
        monitor_scheduler_poll_seconds=monitor_scheduler_poll_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the hosted Testamur account/product preview")
    parser.add_argument("--data-dir", default=".testamur-hosted")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--secure-cookie", action="store_true")
    parser.add_argument("--no-monitor-scheduler", action="store_true")
    parser.add_argument("--monitor-poll-seconds", type=float, default=60.0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    server = build_hosted_server(
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
        secure_cookie=args.secure_cookie,
        monitor_scheduler_enabled=not args.no_monitor_scheduler,
        monitor_scheduler_poll_seconds=args.monitor_poll_seconds,
    )
    print(f"Testamur hosted preview: http://{args.host}:{args.port}/")
    print(
        "Monitor scheduler: "
        + ("disabled" if args.no_monitor_scheduler else f"enabled (poll {args.monitor_poll_seconds:g}s)")
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
