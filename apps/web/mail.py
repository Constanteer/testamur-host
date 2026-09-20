from __future__ import annotations

import hashlib
import html
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote


class MailDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResendMailer:
    api_key: str
    sender: str
    public_origin: str

    @classmethod
    def from_env(cls) -> "ResendMailer | None":
        backend = os.environ.get("TESTAMUR_MAIL_BACKEND", "").strip().casefold()
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
        if not backend:
            if api_key:
                backend = "resend"
            else:
                return None
        if backend != "resend":
            raise RuntimeError(
                f"Unsupported TESTAMUR_MAIL_BACKEND={backend!r}; supported backends: resend"
            )
        if not api_key:
            raise RuntimeError(
                "TESTAMUR_MAIL_BACKEND=resend requires RESEND_API_KEY"
            )
        sender = os.environ.get("TESTAMUR_MAIL_FROM", "").strip()
        origin = os.environ.get("TESTAMUR_PUBLIC_ORIGIN", "").strip().rstrip("/")
        if not sender:
            raise RuntimeError("TESTAMUR_MAIL_FROM must be configured when TESTAMUR_MAIL_BACKEND=resend")
        if not origin:
            raise RuntimeError("TESTAMUR_PUBLIC_ORIGIN must be configured when TESTAMUR_MAIL_BACKEND=resend")
        if not origin.startswith(("https://", "http://")):
            raise RuntimeError("TESTAMUR_PUBLIC_ORIGIN must be an absolute http(s) origin")
        return cls(api_key=api_key, sender=sender, public_origin=origin)

    def send_verification(self, *, user: dict[str, object], token: str) -> str:
        email = str(user.get("email") or "").strip()
        if not email:
            raise MailDeliveryError("verification recipient is missing")
        display_name = str(user.get("display_name") or user.get("username") or "there")
        verify_url = f"{self.public_origin}/verify-email?token={quote(token, safe='')}"
        safe_name = html.escape(display_name)
        safe_url = html.escape(verify_url, quote=True)
        payload = {
            "from": self.sender,
            "to": [email],
            "subject": "Verify your Testamur email",
            "html": (
                "<div style=\"font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;"
                "max-width:560px;margin:0 auto;padding:32px;color:#111827\">"
                "<h1 style=\"font-size:22px;margin:0 0 16px\">Verify your Testamur email</h1>"
                f"<p>Hi {safe_name},</p>"
                "<p>Confirm this email address to activate your Testamur account.</p>"
                f"<p><a href=\"{safe_url}\" style=\"display:inline-block;padding:10px 16px;"
                "background:#111827;color:#fff;text-decoration:none;border-radius:8px\">Verify email</a></p>"
                "<p style=\"font-size:13px;color:#6b7280\">This link expires in 30 minutes. "
                "If you did not create this account, you can ignore this email.</p>"
                "</div>"
            ),
            "tags": [{"name": "category", "value": "email_verification"}],
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": (
                    "verify-email/"
                    + str(user.get("user_id") or "unknown")
                    + "/"
                    + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
                ),
                "User-Agent": "Testamur-Host/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                status = int(getattr(response, "status", response.getcode()))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise MailDeliveryError(f"Resend rejected verification email ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MailDeliveryError(f"Resend verification email request failed: {getattr(exc, 'reason', exc)}") from exc
        if status < 200 or status >= 300:
            raise MailDeliveryError(f"Resend verification email returned HTTP {status}")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        return str(data.get("id") or "")
