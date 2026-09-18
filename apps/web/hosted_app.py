from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
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
from testamur.product_extensions import MonitorTargetProvider
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

SESSION_COOKIE = "testamur_session"
WORKSPACE_COOKIE = "testamur_workspace"
_MATHHUB_PREFIX = "/sources/mathhub"
_ACCOUNT_ROUTES = {"signin", "signup", "settings"}
_PUBLIC_ASSETS = {"index.html", "app.js", "styles.css"}


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
) -> dict[str, Any]:
    path = urlsplit(target).path.rstrip("/") or "/"
    token = _session_token(cookie_header)
    try:
        if path == "/v1/account/signup":
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
            user, session = store.sign_up(
                username=username,
                email=email,
                password=password,
                display_name=display_name,
            )
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

    def active_product_services(self) -> list[TestamurProductService]:
        with self._services_lock:
            return list(self._services.values())

    def server_close(self) -> None:
        self.monitor_scheduler.stop()
        super().server_close()

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
        if path == "/healthz":
            self._send(_json({"ok": True, "service": "testamur-host"}))
            return
        if path == "/scources/mathhub" or path.startswith("/scources/mathhub/"):
            suffix = path[len("/scources/mathhub"):]
            self._send(_redirect(_MATHHUB_PREFIX + (suffix or "/")))
            return
        if path == _MATHHUB_PREFIX:
            self._send(_redirect(_MATHHUB_PREFIX + "/"))
            return
        if path.startswith(_MATHHUB_PREFIX + "/") or path.startswith("/api/"):
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
        if path in {"/signin", "/signup", "/settings/profile", "/settings/security", "/settings/organizations"} or path.startswith("/users/") or path.startswith("/organizations/"):
            self._send(_static(WEB_ROOT / "index.html", cache="no-cache"))
            return
        if name in _PUBLIC_ASSETS:
            self._send(_static(WEB_ROOT / name, cache="public, max-age=300"))
            return

        user = self._current_user()
        if path == "/" and user is None:
            self._send(_redirect("/signin"))
            return
        if path == "/v1" or path.startswith("/v1/"):
            if user is None:
                self._send(_error("authentication_required", "Sign in to use hosted Testamur.", status=401))
                return
            try:
                service = self.hosted_server.product_service_for_workspace(
                    user_id=user["user_id"],
                    workspace_id=_workspace_id(self.headers.get("Cookie")),
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
        self._send(dispatch_web_get(service, self.path))

    def do_POST(self) -> None:
        # Hosted deployments sit behind Zeabur/Cloudflare reverse proxies. Do not
        # reject writes based on the forwarded public Host header here; account
        # and workspace authorization below remains authoritative.
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == _MATHHUB_PREFIX or path.startswith(_MATHHUB_PREFIX + "/") or path.startswith("/api/"):
            self._send(self._proxy_mathhub())
            return

        try:
            payload = self._read_json()
        except AccountError as exc:
            self._send(_error(exc.code, exc.message, status=exc.status))
            return

        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/v1/account/"):
            self._send(
                dispatch_account_write(
                    self.hosted_server.account_store,
                    self.path,
                    payload,
                    cookie_header=self.headers.get("Cookie"),
                    secure_cookie=self.hosted_server.secure_cookie,
                    delete_workspace=self.hosted_server.delete_user_workspace,
                    delete_org_workspace=self.hosted_server.delete_organization_workspace,
                )
            )
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
    return HostedTestamurServer(
        (host, int(port)),
        account_store=AccountStore(root / "accounts.sqlite3"),
        workspace_root=root / "workspaces",
        secure_cookie=secure_cookie,
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
