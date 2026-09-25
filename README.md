# Testamur Host

Private hosted deployment bundle for Testamur.

This repository is intentionally self-contained for deployment. It contains:

- the hosted Testamur account/workspace service;
- a pinned snapshot of the private Testamur core runtime;
- a bundled MathHub runtime;
- one Docker image for Zeabur.

## Public routes

```text
/                       Testamur hosted workspace
/signin                 Sign in
/signup                 Create account
/verify-email            Consume a short-lived email verification link
/verify-email-pending    Verification / resend state
/sources/mathhub/        Bundled public MathHub
/healthz                 Service health
```

`/scources/mathhub[/...]` redirects to the canonical `/sources/mathhub[/...]` path.

The hosted entrypoint does not reject writes based on the public `Host` header. Authentication and workspace/organization authorization remain enforced by the account layer.

## Zeabur

Deploy this repository as **one service** using the root `Dockerfile`.

The container runs two local processes:

```text
0.0.0.0:$PORT
  └─ Testamur hosted gateway
       ├─ Testamur UI/API
       └─ /sources/mathhub/* → 127.0.0.1:8001

127.0.0.1:8001
  └─ MathHub
```

Attach **one persistent Zeabur Volume** at exactly:

```text
/data
```

This is required, not optional. Without a mounted volume, Zeabur redeploys are stateless and `accounts.sqlite3`, projects, monitors, sessions and history are discarded with the old container. After deploying, open `/healthz` and confirm:

```json
"storage": { "persistent_volume_mounted": true }
```

Persistent state is separated inside that volume:

```text
/data/testamur
/data/mathhub
```

The production start command is already defined by the Docker image:

```text
/app/deploy/start.sh
```

Use `https://app.testamur.org` as the hosted-service custom domain. The public marketing/learning surface stays on `https://testamur.org` and is deployed from `testamur-site`. No second public MathHub domain is required.

## Hosted email verification

New hosted accounts are pending until their email address is verified. Testamur stores only a SHA-256 digest of each high-entropy verification token; links expire after 30 minutes and are single-use. A resend is rate-limited and only supersedes the previous delivered link after the mail provider accepts the replacement.

Production delivery uses an explicitly selected mail backend. Resend is currently the supported backend and uses its HTTPS API. Configure all four values as deployment secrets/environment variables, never in the repository:

```text
TESTAMUR_MAIL_BACKEND=resend
RESEND_API_KEY=...
TESTAMUR_PUBLIC_ORIGIN=https://app.testamur.org
TESTAMUR_MAIL_FROM=Testamur <accounts@testamur.org>
```

The service fails closed on partial or unknown mail configuration: an API key alone does not implicitly select a provider, and the Resend backend requires an explicit sender and public origin. This prevents production verification behavior from depending on development defaults.

For an initial private smoke test, Resend's `onboarding@resend.dev` sender can be used where the Resend account permits it. Public signup requires a verified sending domain/address; set `TESTAMUR_MAIL_FROM` to that verified sender before launch.

If the mail backend is not configured, hosted signup fails closed with `email_delivery_unavailable` rather than creating accounts that cannot receive a verification link. Existing accounts created before this schema migration are explicitly grandfathered with `email_verification_source=legacy_migration` so deployment does not lock them out.

## Repository boundaries

`testamur/` is a deployment snapshot of the private canonical core; it is not a second semantic implementation. Hosted account, tenancy, deployment and routing code lives under `apps/`. MathHub remains a separate product/ontology even though it is bundled into the same deployment unit for cost and routing.
