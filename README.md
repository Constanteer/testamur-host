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

Attach one persistent volume at:

```text
/data
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

Use `https://testamur.org` as the public custom domain. No second Zeabur service or public MathHub domain is required.

## Repository boundaries

`testamur/` is a deployment snapshot of the private canonical core; it is not a second semantic implementation. Hosted account, tenancy, deployment and routing code lives under `apps/`. MathHub remains a separate product/ontology even though it is bundled into the same deployment unit for cost and routing.
