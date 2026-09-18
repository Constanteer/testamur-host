from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .runtime_service import EmbeddedRuntimeClient

from . import __version__
from .cli import _render_explanation, _render_impact, main as cli_main
from .environment import discover, git_context
from .environment_status import revalidation_status
from .receipt_store import TestamurReceiptStore
from .verification_store import TestamurVerificationStore


_VERSION_FLAGS = {"--version", "-V"}
_GRAPH_COMMANDS = {"trace", "impact"}


def normalize_argv(argv: Sequence[str]) -> list[str]:
    """Normalize global CLI flags without touching wrapped command arguments."""
    values = list(argv)
    try:
        separator = values.index("--")
    except ValueError:
        head, tail = values, []
    else:
        head, tail = values[:separator], values[separator:]

    machine = "--json" in head
    if machine:
        head = [value for value in head if value != "--json"]

    return (["--json"] if machine else []) + head + tail


def _machine_error(code: str, message: str) -> None:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False, sort_keys=True))


def _render_front_help() -> None:
    print("""usage: testamur [--json] <command> [options]
       testamur [run-options] -- <command> [args...]

A local evidence environment for inspectable work.

commands:
  init                 initialize a Testamur environment
  status               show evidence, verification and revalidation state
  run                  execute a command and persist its evidence
  show <target>        inspect a run, artifact, observation or verification
  why <target>         explain recorded support and verification state
  trace <target>       trace observed provenance upstream
  impact <target>      trace downstream evidence requiring revalidation
  verify <target>      run a checker against a pinned target revision

common workflows:
  testamur init
  testamur -- pytest
  testamur show result.txt
  testamur trace result.txt --recursive
  testamur impact input.csv --recursive --max-depth 8
  testamur verify result.txt --with pytest -- tests/test_result.py

Durable objects such as tst:run:*, tst:obs:* and tst:verification:* can be
inspected with `testamur show`. Recording is evidence, not universal truth.

Use `testamur <command> --help` for command-specific options.
""")


def _short(value: Any, n: int = 18) -> str:
    raw = str(value or "")
    return raw[:n] if raw else "—"


def _render_status(*, env: Any, runtime: dict[str, Any], evidence: dict[str, Any], verification: dict[str, Any], revalidation: dict[str, Any]) -> None:
    git = git_context(env.root)
    print(f"Testamur — {env.config.get('name') or env.root.name}\n")
    print(f"root         {env.root}")
    if git.get("status") == "captured":
        dirty = "dirty" if git.get("dirty") else "clean"
        print(f"git          {_short(git.get('head'), 12)} · {git.get('branch') or 'detached'} · {dirty}")
    else: print("git          not a repository")
    print(f"runtime      {runtime.get('mode') or 'embedded_local'}"); print(f"version      {__version__}")
    print("\nEvidence")
    for label,key in (("runs","runs"),("artifact observations","artifact_observations"),("declared inputs","input_observations"),("declared outputs","output_observations"),("worktree changes","worktree_change_observations")): print(f"  {label:<21} {evidence.get(key,0)}")
    print("\nVerification · current target state")
    for label,key in (("targets","targets"),("records","records"),("checker pass","current_passes"),("checker fail","current_failures"),("stale","stale_records"),("not assessable","not_assessable")): print(f"  {label:<21} {verification.get(key,0)}")
    print("\nRevalidation · dependency propagation")
    for label,key in (("current input paths","paths_current"),("changed input paths","paths_requiring_revalidation"),("not assessable","paths_not_assessable"),("direct runs","direct_runs_requiring_revalidation"),("transitive runs","transitive_runs_requiring_revalidation"),("total runs","total_runs_requiring_revalidation"),("affected observations","affected_observations_requiring_revalidation"),("affected paths","affected_paths_requiring_revalidation")): print(f"  {label:<21} {revalidation.get(key,0)}")
    graph = revalidation.get("dependency_graph") or {}
    if graph: print(f"  graph traversal       {graph.get('paths_traversed',0)} paths · depth {graph.get('max_depth','?')} · {'truncated' if graph.get('truncated') else 'complete within budget'}")
    attention=[item for item in revalidation.get("details") or [] if item.get("state") != "current"]
    if attention:
        print("\nNeeds attention")
        for item in attention[:8]:
            state=item.get("state") or "unknown"; marker="△" if state=="requires_revalidation" else "?"; direct=len(item.get("runs_requiring_revalidation") or []); transitive=len(item.get("transitive_runs_requiring_revalidation") or []); suffix=f" · {direct} direct" if state=="requires_revalidation" else ""; suffix += f" + {transitive} transitive" if transitive and state=="requires_revalidation" else ""; print(f"  {marker} {item.get('path')}  {state}{suffix}")
        if revalidation.get("details_truncated") or len(attention)>8: print("  …")
    print("\nNo universal trust score is computed.")
    print("Revalidation propagates uncertainty through revision-matched evidence edges; it does not prove downstream conclusions false.")
    print("Use `testamur impact <path> --recursive` to inspect the downstream chain.")


def _try_status(effective:list[str],*,machine:bool)->int|None:
    if effective != ["status"] or machine: return None
    env=discover()
    if env is None: print("testamur: not inside a Testamur environment; run `testamur init` first",file=sys.stderr); return 1
    receipts=TestamurReceiptStore(env.database_path); verification_store=TestamurVerificationStore(receipts); runtime=EmbeddedRuntimeClient(env.database_path).status()
    _render_status(env=env,runtime=runtime,evidence=receipts.stats(),verification=verification_store.stats(root=env.root),revalidation=revalidation_status(receipts,root=env.root)); return 0


def _render_verification_record(value:dict[str,Any])->None:
    print("Testamur show — verification\n"); print(f"verification  {value.get('verification_id') or '—'}"); print(f"target        {value.get('target_ref') or '—'}"); print(f"target kind   {value.get('target_kind') or '—'}"); print(f"checker       {value.get('verifier') or '—'}"); print(f"checker run   {value.get('checker_run_id') or '—'}"); print(f"result        {value.get('checker_status') or 'unknown'}"); print(f"revision      {value.get('revision_state') or 'unknown'}")
    if value.get("target_content_hash"): print(f"target hash   {str(value.get('target_content_hash'))[:22]}")
    print("\nThis record is immutable checker evidence; it is not a universal truth assertion.")


def _render_observation(value:dict[str,Any])->None:
    print("Testamur show — observation\n"); print(f"observation   {value.get('observation_id') or '—'}"); print(f"run           {value.get('run_id') or '—'}"); print(f"role          {value.get('role') or 'unknown'}"); print(f"path          {value.get('declared_path') or value.get('resolved_path') or '—'}"); print(f"status        {value.get('status') or 'unknown'}")
    if value.get("content_hash"): print(f"content hash  {str(value.get('content_hash'))[:22]}")
    observation=value.get("observation")
    if isinstance(observation,dict) and observation.get("change_kind"): print(f"change        {observation.get('change_kind')}")
    print("\nThis is an immutable recorded observation; observation alone does not imply causality or correctness.")


def _render_recursive_trace(value:dict[str,Any])->None:
    _render_explanation("trace",value); transitive=value.get("transitive_upstream_runs") or []
    if transitive:
        print("\ntransitive upstream observed runs")
        for item in transitive: print(f"  ↑{item.get('depth') or '?'} {item.get('run_id')}  [{item.get('observation_role') or 'observation'}] via {item.get('via_artifact') or '?'}  revision={item.get('revision_relation') or 'unknown'}  {' '.join(str(x) for x in item.get('command') or [])}")
    mismatched=value.get("revision_mismatched_producers") or []
    if mismatched:
        print("\nhistorical producer revisions skipped")
        for item in mismatched[:8]: print(f"  ×{item.get('depth') or '?'} {item.get('run_id')}  via {item.get('via_artifact') or '?'}  different_revision")
        if len(mismatched)>8: print(f"  … {len(mismatched)-8} more")
    traversal=value.get("traversal") or {}
    if traversal.get("recursive"):
        print(f"\ntraversal    {traversal.get('runs_observed',0)} runs · {traversal.get('edges_observed',0)} edges · {traversal.get('revision_mismatches',0)} revision mismatches · max depth {traversal.get('max_depth')}")
        if traversal.get("truncated"): print("traversal    truncated at requested max depth")


def _render_recursive_impact(value:dict[str,Any])->None:
    _render_impact(value); transitive=value.get("transitive_consumers") or []
    if transitive:
        print("\ntransitive consumers requiring revalidation")
        for item in transitive: print(f"  ↓{item.get('depth') or '?'} {item.get('run_id')}  via {item.get('via_artifact') or '?'}  revision={item.get('revision_relation') or 'unknown'}  {' '.join(str(x) for x in item.get('command') or [])}")
    mismatched=value.get("revision_mismatched_edges") or []
    if mismatched:
        print("\nhistorical revision edges skipped")
        for item in mismatched[:8]: print(f"  ×{item.get('depth') or '?'} {item.get('source_run_id')} → {item.get('consumer_run_id')}  via {item.get('artifact')}  different_revision")
        if len(mismatched)>8: print(f"  … {len(mismatched)-8} more")
    traversal=value.get("traversal") or {}
    if traversal.get("recursive"):
        print(f"\ntraversal    {traversal.get('immediate_consumers',0)} immediate + {traversal.get('transitive_consumers',0)} transitive consumers · {traversal.get('revision_mismatches',0)} revision mismatches · max depth {traversal.get('max_depth')}")
        if traversal.get("truncated"): print("traversal    truncated at requested max depth")


def _decorate_verification_for_inspection(verification:dict[str,Any],*,verifications:TestamurVerificationStore,root:Any)->dict[str,Any]:
    state=verifications.for_target(str(verification.get("target_ref") or ""),root=root); revision_state="unknown"
    for item in state.get("records") or []:
        if item.get("verification_id")==verification.get("verification_id"): revision_state=str(item.get("revision_state") or "unknown"); break
    return {**verification,"revision_state":revision_state,"target_verification_state":state.get("verification_state")}


def _try_local_show(effective:list[str],*,machine:bool)->int|None:
    if len(effective)!=2 or effective[0]!="show": return None
    env=discover()
    if env is None:return None
    target=effective[1]; receipts=TestamurReceiptStore(env.database_path); verifications=TestamurVerificationStore(receipts)
    if receipts.get(target) is not None:return None
    observation=receipts.get_observation(target)
    if observation is not None:
        print(json.dumps({"kind":"observation",**observation},ensure_ascii=False,sort_keys=True)) if machine else _render_observation(observation); return 0
    verification=verifications.get(target)
    if verification is not None:
        inspected=_decorate_verification_for_inspection(verification,verifications=verifications,root=env.root); print(json.dumps({"kind":"verification",**inspected},ensure_ascii=False,sort_keys=True)) if machine else _render_verification_record(inspected); return 0
    try:value=receipts.explain(target,root=env.root)
    except KeyError:return None
    if value.get("kind")!="artifact":return None
    value["verification_records"]=verifications.for_target(target,root=env.root); print(json.dumps(value,ensure_ascii=False,sort_keys=True)) if machine else _render_explanation("show",value); return 0


def _graph_parser(command:str)->argparse.ArgumentParser:
    description="trace persisted provenance observations upstream" if command=="trace" else "show downstream evidence that requires revalidation"; parser=argparse.ArgumentParser(prog=f"testamur {command}",description=description); parser.add_argument("id",help="artifact path or durable run/object id"); parser.add_argument("--recursive",action="store_true",help="walk the persisted evidence graph transitively"); parser.add_argument("--max-depth",type=int,default=8,metavar="N",help="maximum graph hop depth when --recursive is used (default: 8)"); return parser


def _try_graph_command(effective:list[str],*,machine:bool)->int|None:
    if not effective or effective[0] not in _GRAPH_COMMANDS:return None
    command=effective[0]; has_graph_options=any(value=="--recursive" or value=="--max-depth" or value.startswith("--max-depth=") for value in effective[1:])
    if not has_graph_options and "--help" not in effective[1:] and "-h" not in effective[1:]:return None
    parser=_graph_parser(command)
    try:args=parser.parse_args(effective[1:])
    except SystemExit as exc:return int(exc.code) if isinstance(exc.code,int) else 2
    env=discover()
    if env is None:
        if machine:_machine_error("environment_required","not inside a Testamur environment; run `testamur init` first")
        else:print("testamur: not inside a Testamur environment; run `testamur init` first",file=sys.stderr)
        return 1
    receipts=TestamurReceiptStore(env.database_path); verifications=TestamurVerificationStore(receipts)
    try:
        if command=="trace":value=receipts.trace(args.id,root=env.root,recursive=args.recursive,max_depth=args.max_depth); value["verification_records"]=verifications.for_target(args.id,root=env.root); renderer="explanation"
        else:value=receipts.impact(args.id,root=env.root,recursive=args.recursive,max_depth=args.max_depth); value["verification_records"]=verifications.for_target(args.id,root=env.root); renderer="impact"
    except (KeyError,ValueError,OSError) as exc:
        if machine:_machine_error(type(exc).__name__,str(exc))
        else:print(f"testamur: {exc}",file=sys.stderr)
        return 1
    if machine:print(json.dumps(value,ensure_ascii=False,sort_keys=True))
    elif renderer=="explanation":_render_recursive_trace(value)
    else:_render_recursive_impact(value)
    return 0


def main(argv:Sequence[str]|None=None)->int:
    raw=list(sys.argv[1:] if argv is None else argv)
    if not raw or raw in (["--help"],["-h"]):_render_front_help();return 0
    before_separator=raw[:raw.index("--")] if "--" in raw else raw
    if any(flag in before_separator for flag in _VERSION_FLAGS):
        if "--json" in before_separator:print(json.dumps({"ok":True,"testamur_version":__version__},ensure_ascii=False,sort_keys=True))
        else:print(f"testamur {__version__}")
        return 0
    normalized=normalize_argv(raw);machine=bool(normalized and normalized[0]=="--json");effective=normalized[1:] if machine else normalized
    if not effective:
        if machine:_machine_error("missing_command","a Testamur command is required")
        return 2
    elif effective==["run"] or effective==["--"]:
        if machine:_machine_error("missing_wrapped_command","no command was provided to run")
        else:print("testamur: no command was provided to run",file=sys.stderr)
        return 2
    status_result=_try_status(effective,machine=machine)
    if status_result is not None:return status_result
    local_show=_try_local_show(effective,machine=machine)
    if local_show is not None:return local_show
    graph_result=_try_graph_command(effective,machine=machine)
    if graph_result is not None:return graph_result
    try:return int(cli_main(normalized))
    except SystemExit as exc:return int(exc.code) if isinstance(exc.code,int) else 1
