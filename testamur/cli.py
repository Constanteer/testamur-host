from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agent_wrapper import run_agent_command
from .runtime_graph_query import local_query as _local_query

from . import __version__
from .environment import TestamurEnvironment, discover, git_context, initialize
from .environment_status import revalidation_status
from .receipt_store import TestamurReceiptStore
from .runtime_inspect import inspect_run
from .runtime_service import EmbeddedRuntimeClient
from .verification_store import TestamurVerificationStore

COMMANDS = {"init", "status", "run", "show", "why", "trace", "impact", "verify"}


def _prepare_argv(argv: list[str]) -> list[str]:
    values = list(argv)
    prefix: list[str] = []
    while values and values[0] == "--json":
        prefix.append(values.pop(0))
    if values and values[0] == "--":
        return [*prefix, "run", *values]
    return [*prefix, *values]


def _require_environment() -> TestamurEnvironment:
    env = discover()
    if env is None:
        raise RuntimeError("not inside a Testamur environment; run `testamur init` first")
    return env


def _client(env: TestamurEnvironment) -> EmbeddedRuntimeClient:
    return EmbeddedRuntimeClient(env.database_path)


def _receipts(env: TestamurEnvironment) -> TestamurReceiptStore:
    return TestamurReceiptStore(env.database_path)


def _short(value: str | None, n: int = 12) -> str:
    raw = str(value or "")
    return raw[:n] if raw else "—"


def _render_init(env: TestamurEnvironment, runtime: dict[str, Any]) -> None:
    git = git_context(env.root)
    print("Testamur initialized\n")
    print(f"project      {env.config.get('name') or env.root.name}")
    print(f"root         {env.root}")
    if git.get("status") == "captured":
        dirty = "dirty" if git.get("dirty") else "clean"
        print(f"git          {_short(git.get('head'))} · {git.get('branch') or 'detached'} · {dirty}")
    else:
        print("git          not a repository")
    print(f"store        {env.database_path.relative_to(env.root)}")
    print(f"runtime      {runtime.get('mode') or 'embedded_local'}")
    print("\nNothing is trusted just because it was recorded.")
    print("Run work through Testamur to begin building evidence:\n")
    print("  testamur -- pytest")


def _render_status(
    env: TestamurEnvironment,
    runtime: dict[str, Any],
    evidence: dict[str, int],
    revalidation: dict[str, Any],
    verification: dict[str, Any],
) -> None:
    git = git_context(env.root)
    print(f"Testamur — {env.config.get('name') or env.root.name}\n")
    print(f"root         {env.root}")
    if git.get("status") == "captured":
        dirty = "dirty" if git.get("dirty") else "clean"
        print(f"git          {_short(git.get('head'))} · {git.get('branch') or 'detached'} · {dirty}")
    else:
        print("git          not a repository")
    print(f"store        {env.database_path.relative_to(env.root)}")
    print(f"runtime      {runtime.get('mode') or 'embedded_local'}")

    print("\nEvidence")
    print(f"  runs                  {evidence.get('runs', 0)}")
    print(f"  artifact observations {evidence.get('artifact_observations', 0)}")
    print(f"  declared inputs       {evidence.get('input_observations', 0)}")
    print(f"  declared outputs      {evidence.get('output_observations', 0)}")
    print(f"  worktree changes      {evidence.get('worktree_change_observations', 0)}")

    print("\nVerification")
    print(f"  records               {verification.get('records', 0)}")
    print(f"  current checker pass  {verification.get('current_passes', 0)}")
    print(f"  current checker fail  {verification.get('current_failures', 0)}")
    print(f"  stale                 {verification.get('stale_records', 0)}")
    print(f"  not assessable        {verification.get('not_assessable', 0)}")

    print("\nRevalidation")
    print(f"  current paths         {revalidation.get('paths_current', 0)}")
    print(f"  require revalidation  {revalidation.get('paths_requiring_revalidation', 0)}")
    print(f"  not assessable        {revalidation.get('paths_not_assessable', 0)}")
    print(f"  affected runs         {revalidation.get('runs_requiring_revalidation', 0)}")

    attention = [
        item
        for item in revalidation.get("details") or []
        if item.get("state") != "current"
    ]
    if attention:
        print("\nNeeds attention")
        for item in attention[:8]:
            state = item.get("state") or "unknown"
            marker = "△" if state == "requires_revalidation" else "?"
            print(f"  {marker} {item.get('path')}  {state}")
        if revalidation.get("details_truncated") or len(attention) > 8:
            print("  …")

    print("\nNo universal trust score is computed.")
    print("A changed input makes dependent evidence require revalidation; it does not prove the conclusion false.")
    print("Use `testamur why <path|id>` and `testamur impact <path|id>` to inspect the chain.")


def _render_run(value: dict[str, Any]) -> None:
    state = str(value.get("state") or "unknown")
    code = value.get("exit_code")
    run_id = str(value.get("run_id") or "")
    print(f"{'✓' if code == 0 else '✗'} command finished")
    print(f"\nrun          {run_id or '—'}")
    print(f"state        {state}")
    print(f"exit         {code if code is not None else 'unknown'}")
    git = value.get("git_context") or value.get("git")
    if isinstance(git, dict):
        after = git.get("after") if isinstance(git.get("after"), dict) else git
        if after.get("head_sha"):
            dirty = "dirty" if after.get("dirty") else "clean"
            print(f"git          {_short(str(after.get('head_sha')))} · {after.get('branch') or 'detached'} · {dirty}")
    delta = value.get("worktree_delta")
    if isinstance(delta, dict) and delta.get("status") == "captured":
        count = sum(len(delta.get(key) or []) for key in ("added", "removed", "changed"))
        print(f"changes      {count} observed")
    print(f"verified     {'yes' if value.get('verification_implied') else 'no'}")
    print("\nThis run produced evidence, not verification.")
    if run_id:
        print(f"Inspect with: testamur show {run_id}")
    stdout = str(value.get("stdout") or "")
    stderr = str(value.get("stderr") or "")
    if stdout:
        print("\n── stdout ──")
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print("\n── stderr ──", file=sys.stderr)
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")


def _display_artifact(item: dict[str, Any]) -> str:
    path = item.get("declared_path") or item.get("resolved_path") or "?"
    digest = item.get("content_hash")
    if not digest:
        observation = item.get("observation")
        if isinstance(observation, dict):
            snapshot = observation.get("snapshot")
            if isinstance(snapshot, dict):
                digest = snapshot.get("content_hash")
    return f"{path}  {_short(str(digest), 18) if digest else item.get('status', 'unknown')}"


def _render_verification_summary(summary: dict[str, Any]) -> None:
    print("\nverification records")
    print(f"  state        {summary.get('verification_state') or 'not_verified'}")
    records = summary.get("records") or []
    if not records:
        print("  records      none")
        return
    for item in records[:6]:
        checker = item.get("verifier") or "checker"
        status = item.get("checker_status") or "unknown"
        revision = item.get("revision_state") or "unknown"
        print(f"  {checker:<12} {status:<8} target={revision}  run={item.get('checker_run_id')}")
    if len(records) > 6:
        print(f"  … {len(records) - 6} more")
    print("  checker pass is scoped evidence, not universal truth")


def _render_explanation(kind: str, value: dict[str, Any]) -> None:
    print(f"Testamur {kind} — {value.get('target') or value.get('run_id') or ''}\n")
    if value.get("kind") == "run":
        command = " ".join(str(x) for x in value.get("command") or [])
        print(f"run          {value.get('target')}")
        print(f"state        {value.get('state') or 'unknown'}")
        print(f"command      {command or '—'}")
        inputs = value.get("inputs") or []
        outputs = value.get("outputs") or []
        if inputs:
            print("\ndeclared inputs")
            for item in inputs:
                if isinstance(item, dict):
                    print(f"  ← {_display_artifact(item)}")
        if outputs:
            print("\ndeclared outputs")
            for item in outputs:
                if isinstance(item, dict):
                    print(f"  → {_display_artifact(item)}")
        delta = value.get("worktree_delta")
        if isinstance(delta, dict) and delta.get("status") == "captured":
            changes = [
                *(str(x) for x in delta.get("added") or []),
                *(str(x) for x in delta.get("changed") or []),
                *(str(x) for x in delta.get("removed") or []),
            ]
            if changes:
                print("\nworktree observed changed during run")
                for path in changes:
                    print(f"  ~ {path}")
                print("  causal attribution: not implied")
        summary = value.get("verification_records")
        if isinstance(summary, dict):
            _render_verification_summary(summary)
        else:
            print(f"\nverification {value.get('verification') or 'not examined'}")
        print("\nExecution success is evidence of execution, not proof of correctness.")
        return

    if value.get("kind") == "artifact":
        print(f"artifact     {value.get('target')}")
        current = value.get("current_content_hash")
        print(f"current      {_short(str(current), 22) if current else 'missing / unavailable'}")

        declared_outputs = value.get("observed_as_declared_output") or []
        inputs = value.get("observed_as_input") or []
        worktree = value.get("observed_worktree_changes") or []

        if declared_outputs:
            print("\nobserved as declared output")
            for item in declared_outputs:
                print(f"  ← {item.get('run_id')}  {_display_artifact(item)}")
        if worktree:
            print("\nobserved changed during run")
            for item in worktree:
                observation = item.get("observation") if isinstance(item.get("observation"), dict) else {}
                change_kind = observation.get("change_kind") or "changed"
                print(f"  ~ {item.get('run_id')}  {change_kind}  {_display_artifact(item)}")
            print("  causal attribution: not implied")
        if not declared_outputs and not worktree:
            print("\norigin observation  none")

        if inputs:
            print("\nobserved as input")
            for item in inputs:
                print(f"  → {item.get('run_id')}  {_display_artifact(item)}")

        upstream = value.get("upstream_observed_runs") or []
        if upstream:
            print("\nupstream observed runs")
            for item in upstream:
                command = " ".join(str(x) for x in item.get("command") or [])
                role = item.get("observation_role") or "observation"
                print(f"  {item.get('run_id')}  [{role}]  {command}")

        summary = value.get("verification_records")
        if isinstance(summary, dict):
            _render_verification_summary(summary)
        else:
            print(f"\nverification {value.get('verification') or 'not inferred'}")
        if value.get("causal_attribution_implied") is False:
            print("causality    not inferred from temporal observation")
        return

    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _render_impact(value: dict[str, Any]) -> None:
    print(f"Testamur impact — {value.get('target') or ''}\n")
    print(
        f"current      {_short(str(value.get('current_content_hash')), 22) if value.get('current_content_hash') else 'missing / unavailable'}"
    )
    consumers = value.get("consumers") or []
    if not consumers:
        print("consumers    none observed")
    else:
        print("consumers")
        for item in consumers:
            command = " ".join(str(x) for x in item.get("command") or [])
            print(f"  {item.get('state')}  {item.get('run_id')}  {command}")
    observations = value.get("potentially_affected_observations") or []
    if observations:
        print("\npotentially affected observations")
        for item in observations:
            role = item.get("observation_role") or "observation"
            print(f"  △ {item.get('path')}  [{role}] via {item.get('run_id')}")
    summary = value.get("verification_records")
    if isinstance(summary, dict):
        _render_verification_summary(summary)
    print("\nChange does not automatically prove invalidity; affected evidence requires revalidation.")
    if (value.get("semantics") or {}).get("causal_attribution_implied") is False:
        print("Temporal co-observation does not by itself establish causation.")


def _render_verification_result(value: dict[str, Any]) -> None:
    record = value["verification"]
    state = value["state"]
    passed = record.get("checker_status") == "passed"
    print(f"{'✓' if passed else '✗'} checker finished\n")
    print(f"target       {value.get('target')}")
    print(f"checker      {record.get('verifier')}")
    print(f"checker run  {record.get('checker_run_id')}")
    print(f"result       {record.get('checker_status')}")
    print(f"revision     {state.get('verification_state')}")
    if record.get("target_content_hash"):
        print(f"target hash  {_short(str(record.get('target_content_hash')), 22)}")
    print("\nA checker pass is evidence for this pinned target revision, not universal truth.")
    if state.get("verification_state") == "stale":
        print("The target changed during or after checking; this verification is already stale.")


def _render_generic(kind: str, value: dict[str, Any]) -> None:
    print(f"Testamur {kind}\n")
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testamur",
        description="A local evidence environment for work you need to inspect later.",
    )
    parser.add_argument("--json", action="store_true", dest="machine")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize a Testamur environment in this directory")
    p.add_argument("--name")

    sub.add_parser("status", help="show evidence and revalidation state for this environment")

    p = sub.add_parser("run", help="run a command inside the evidence environment")
    p.add_argument("--cwd")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--idempotency-key")
    p.add_argument("--input", action="append", default=[], dest="input_artifacts")
    p.add_argument("--output", action="append", default=[], dest="output_artifacts")
    p.add_argument("--worktree-delta", action="store_true")
    p.add_argument("--no-git-context", action="store_true")
    p.add_argument("command_argv", nargs=argparse.REMAINDER)

    p = sub.add_parser("show", help="inspect one recorded run")
    p.add_argument("id")

    for name, help_text in (
        ("why", "show why a target is believed or where its evidence comes from"),
        ("trace", "trace upstream observations for a target"),
        ("impact", "show what evidence requires revalidation if a target changes"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id")

    p = sub.add_parser("verify", help="run a checker and bind its evidence to a target revision")
    p.add_argument("id")
    p.add_argument("--with", dest="verifier", required=True, help="checker executable, e.g. pytest")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("checker_args", nargs="*")
    return parser


def _environment_json(
    env: TestamurEnvironment,
    runtime: dict[str, Any],
    evidence: dict[str, int],
    revalidation: dict[str, Any],
    verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "testamur_version": __version__,
        "environment": {
            "root": str(env.root),
            "name": env.config.get("name") or env.root.name,
            "database": str(env.database_path),
            "git": git_context(env.root),
        },
        "runtime": runtime,
        "evidence": evidence,
        "verification": verification,
        "revalidation": revalidation,
    }


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(_prepare_argv(raw))
    try:
        if args.command == "init":
            env = initialize(name=args.name)
            runtime = _client(env).status()
            receipts = _receipts(env)
            verifications = TestamurVerificationStore(receipts)
            evidence = receipts.stats()
            revalidation = revalidation_status(receipts, root=env.root)
            verification = verifications.stats(root=env.root)
            value = _environment_json(env, runtime, evidence, revalidation, verification)
            if args.machine:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                _render_init(env, runtime)
            return 0

        env = _require_environment()
        client = _client(env)
        receipts = _receipts(env)
        verifications = TestamurVerificationStore(receipts)

        if args.command == "status":
            runtime = client.status()
            evidence = receipts.stats()
            revalidation = revalidation_status(receipts, root=env.root)
            verification = verifications.stats(root=env.root)
            value = _environment_json(env, runtime, evidence, revalidation, verification)
            if args.machine:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                _render_status(env, runtime, evidence, revalidation, verification)
            return 0

        if args.command == "run":
            command_argv = list(args.command_argv)
            if command_argv and command_argv[0] == "--":
                command_argv = command_argv[1:]
            cwd = Path(args.cwd).expanduser().resolve() if args.cwd else env.root
            value = run_agent_command(
                client,
                command_argv,
                cwd=cwd,
                timeout_seconds=args.timeout,
                client_id="testamur-cli",
                idempotency_key=args.idempotency_key,
                input_artifacts=args.input_artifacts,
                output_artifacts=args.output_artifacts,
                capture_git_context=not args.no_git_context,
                capture_worktree_delta=args.worktree_delta,
            )
            value["testamur_version"] = __version__
            value["artifact_observations_persisted"] = True
            value["testamur_receipt"] = receipts.record(value)
            if args.machine:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                _render_run(value)
            if value.get("caller_interrupted"):
                return int(value.get("caller_exit_code") or 130)
            signal_number = value.get("signal_number")
            if signal_number is not None:
                return 128 + int(signal_number)
            return int(value.get("exit_code") or 0)

        if args.command == "verify":
            target_revision = verifications.prepare_target_revision(args.id, root=env.root)
            checker_args = list(args.checker_args)
            if checker_args and checker_args[0] == "--":
                checker_args = checker_args[1:]
            checker_argv = [str(args.verifier), *checker_args]
            checker_inputs = (
                [args.id] if target_revision.get("target_kind") == "artifact_path" else []
            )
            checker = run_agent_command(
                client,
                checker_argv,
                cwd=env.root,
                timeout_seconds=args.timeout,
                client_id="testamur-verifier",
                input_artifacts=checker_inputs,
                output_artifacts=[],
                capture_git_context=True,
                capture_worktree_delta=True,
            )
            checker["testamur_version"] = __version__
            checker["artifact_observations_persisted"] = True
            checker["testamur_receipt"] = receipts.record(checker)
            record = verifications.record(
                args.id,
                root=env.root,
                verifier=args.verifier,
                checker_receipt=checker,
                target_revision=target_revision,
            )
            state = verifications.for_target(args.id, root=env.root)
            value = {
                "target": args.id,
                "verification": record,
                "state": state,
                "checker_run": checker,
            }
            if args.machine:
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                _render_verification_result(value)
            if checker.get("caller_interrupted"):
                return int(checker.get("caller_exit_code") or 130)
            signal_number = checker.get("signal_number")
            if signal_number is not None:
                return 128 + int(signal_number)
            return int(checker.get("exit_code") or 0)

        if args.command == "show":
            stored = receipts.get(args.id)
            if stored is not None:
                value = receipts.explain(args.id, root=env.root)
                value["verification_records"] = verifications.for_target(args.id, root=env.root)
                renderer = "explanation"
            else:
                value = inspect_run(client, args.id, client_id="testamur-cli")
                renderer = "generic"
        elif args.command == "why":
            try:
                value = receipts.explain(args.id, root=env.root)
                value["verification_records"] = verifications.for_target(args.id, root=env.root)
                renderer = "explanation"
            except KeyError:
                value = _local_query(client, args.id, args.command)
                renderer = "generic"
        elif args.command == "trace":
            try:
                value = receipts.trace(args.id, root=env.root)
                value["verification_records"] = verifications.for_target(args.id, root=env.root)
                renderer = "explanation"
            except KeyError:
                value = _local_query(client, args.id, args.command)
                renderer = "generic"
        elif args.command == "impact":
            try:
                value = receipts.impact(args.id, root=env.root)
                value["verification_records"] = verifications.for_target(args.id, root=env.root)
                renderer = "impact"
            except KeyError:
                value = _local_query(client, args.id, args.command)
                renderer = "generic"
        else:
            raise ValueError(f"unsupported command {args.command}")

        if args.machine:
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        elif renderer == "explanation":
            _render_explanation(args.command, value)
        elif renderer == "impact":
            _render_impact(value)
        else:
            _render_generic(args.command, value)
        return 0
    except KeyboardInterrupt:
        print("testamur: interrupted", file=sys.stderr)
        return 130
    except (KeyError, RuntimeError, ValueError, OSError) as exc:
        if getattr(args, "machine", False):
            print(
                json.dumps(
                    {"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(f"testamur: {exc}", file=sys.stderr)
        return 1
