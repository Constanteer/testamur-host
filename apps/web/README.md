# Testamur Web

Deployment-facing Testamur application boundary.

The canonical local alpha UI remains under `testamur/web/` so the public core package and local release gate stay account-free. Hosted identity and service-plane behavior lives here and depends on Testamur core/ProductService instead of redefining Testamur semantics.

## Hosted account preview

A real hosted account/session vertical slice is available for local product testing:

```bash
python -m apps.web.hosted_app --data-dir .testamur-hosted
```

Open `http://127.0.0.1:8788/`. Anonymous visitors are sent to sign in; new users can sign up directly. The preview currently provides:

- username/email/password registration;
- scrypt password hashing;
- HttpOnly, SameSite=Lax session cookies;
- sign in / sign out and 30-day session expiry;
- profile display-name editing and public profile pages that do not expose email;
- active-session inspection and per-session / all-other-session revocation;
- password rotation that preserves the current session and revokes the rest;
- confirmed account deletion that removes the account, sessions, and private hosted workspace;
- an isolated Testamur ProductService database per user;
- the existing projects, monitoring, history and evidence UI after authentication.

For HTTPS deployment, pass `--secure-cookie`.

This is a transition implementation, not the final production service plane. Organization/team membership, invitations, shared workspaces, email verification/recovery, production rate limiting, audit administration, billing entitlements and deployment secrets remain hosted-platform work.

## Boundary rule

This layer may depend on Testamur core and its ProductService contracts. It must not own a duplicate semantic store, policy engine, reliance engine, temporal model, affectedness model, or lineage engine. The public local CLI and `testamur-web` must remain usable without creating a hosted account.
