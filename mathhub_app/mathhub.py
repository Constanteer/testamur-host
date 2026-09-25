#!/usr/bin/env python3
"""Canonical MathHub local server: public IDs + bounded mathematical reads."""
from __future__ import annotations

import json,os,re,sqlite3,threading,time
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs,urlparse

import dependency_import as dependency_import_module
import import_tasks as import_tasks_module
import server as core
from batch_import import import_existing_declarations
from claim_import import import_existing_declaration
from coverage_index import CoverageIndex, install_classifier_cache
from dependency_import import import_dependency_closure
from graph_display import GraphDisplay
from graph_query_catalog import GraphQueryCatalog as Catalog
from graph_world import GraphWorld
from history import HistoryReader
from import_tasks import ImportTaskStore
from namespace_browser import (
    ensure_global_library_scan,
    global_library_cluster,
    global_library_module,
    global_library_status,
    global_library_world,
    library_theorem_detail,
    library_declaration_dependencies,
    random_library_theorem,
    list_namespace_declarations,
)
from ontology import validate_claim_declaration
from presentation import PresentationIndex, canonical_from_declaration, suggested_title
from public_identity import PublicIdentity
from source_provenance import SourceProvenance
from source_ingest import ingest_source

IDENTITY=PublicIdentity(core.DB_PATH); CATALOG=Catalog(core.DB_PATH); HISTORY=HistoryReader(core.DB_PATH); PRESENTATIONS=PresentationIndex(core.DB_PATH); WORLD=GraphWorld(core.DB_PATH,core.ARGUMENT); GRAPH_DISPLAY=GraphDisplay(core.DB_PATH); TASKS=ImportTaskStore(core.DB_PATH); COVERAGE=CoverageIndex(core.DB_PATH); SOURCE_PROVENANCE=SourceProvenance(core.DB_PATH)
AUTO_IMPORT_TASKS=os.environ.get("MATHHUB_AUTO_IMPORT_TASKS","1").strip().lower() not in {"0","false","no","off"}
IMPORT_WORKER_INTERVAL_SECONDS=max(0.5,float(os.environ.get("MATHHUB_IMPORT_WORKER_INTERVAL","1.5")))
IMPORT_LEASE_STALE_SECONDS=max(60,float(os.environ.get("MATHHUB_IMPORT_LEASE_STALE_SECONDS","900")))
AUTO_INDEX_NAMESPACES=tuple(
    part.strip() for part in os.environ.get("MATHHUB_AUTO_INDEX_NAMESPACES","Nat").split(",") if part.strip()
)
LIBRARY_WARMUP_POLL_SECONDS=max(0.5,float(os.environ.get("MATHHUB_LIBRARY_WARMUP_POLL","2")))
AUTO_STARTER_CLAIMS=os.environ.get("MATHHUB_AUTO_STARTER_CLAIMS","0").strip().lower() not in {"0","false","no","off"}
STARTER_CLAIM_ROOTS=tuple(
    part.strip() for part in os.environ.get(
        "MATHHUB_STARTER_DECLARATIONS",
        "Nat.add_comm,Nat.add_assoc,Nat.mul_comm,Nat.mul_assoc,List.append_assoc,Set.union_comm,Set.inter_comm",
    ).split(",") if part.strip()
)
STARTER_CLAIM_MAX_DEPTH=max(0,min(2,int(os.environ.get("MATHHUB_STARTER_MAX_DEPTH","1"))))
STARTER_CLAIM_MAX_CLAIMS=max(len(STARTER_CLAIM_ROOTS),min(240,int(os.environ.get("MATHHUB_STARTER_MAX_CLAIMS","96"))))
_IMPORT_WORKER_STARTED=False
_IMPORT_WORKER_LOCK=threading.Lock()
_LIBRARY_WARMUP_STARTED=False
_LIBRARY_WARMUP_LOCK=threading.Lock()
_STARTER_BOOTSTRAP_STARTED=False
_STARTER_BOOTSTRAP_LOCK=threading.Lock()
install_classifier_cache(COVERAGE,dependency_import_module,import_tasks_module,core.runtime_status)
INTERNAL_RE=re.compile(r"^(CLM|VER|PRF|BLD)-(\d+)$",re.I); PUBLICISH=r"(?:CLM|VER|PRF|BLD)-[A-Z0-9-]+"; TASKISH=r"IMT-[A-F0-9]{16}"
P2K={"CLM":"claim","VER":"version","PRF":"proof","BLD":"build"}; K2P={v:k for k,v in P2K.items()}

def _ctx(x:dict[str,Any])->tuple[str,str]:
    c=x.get("current") if isinstance(x.get("current"),dict) else {}; return str(x.get("title") or c.get("title") or ""),str(x.get("lean_name") or c.get("lean_name") or "")
def pubref(v:str,ctx:dict[str,Any]|None=None)->str:
    m=INTERNAL_RE.fullmatch(str(v or "").strip())
    if not m:return v
    p,n=m.group(1).upper(),int(m.group(2)); k=P2K[p]
    if k=="claim":
        title,name=_ctx(ctx or {})
        if not (title or name):
            try:title,name=_ctx(core.REGISTRY.get_claim(f"CLM-{n:06d}"))
            except (KeyError,ValueError):pass
        return IDENTITY.ensure_claim(n,title,name)["public_id"]
    if k=="version":return IDENTITY.ensure_version(n)["public_id"]
    if k=="proof":return IDENTITY.ensure_proof(n)["public_id"]
    return IDENTITY.ensure_build(n)["public_id"]
def internal(v:str,kind:str)->str:
    raw=str(v or "").strip().upper(); m=INTERNAL_RE.fullmatch(raw)
    if m:
        if P2K[m.group(1).upper()]!=kind:raise ValueError(f"expected {kind} reference")
        return raw
    k,n=IDENTITY.resolve(raw,kind); return f"{K2P[k]}-{n:06d}"
def iid(v:str,kind:str)->int:
    m=INTERNAL_RE.fullmatch(internal(v,kind));
    if not m:raise ValueError(f"could not resolve {kind} reference")
    return int(m.group(2))
def publicize(v:Any,ctx:dict[str,Any]|None=None)->Any:
    if isinstance(v,list):return [publicize(x,ctx) for x in v]
    if isinstance(v,dict):
        out={k:publicize(x,v) for k,x in v.items()}
        raw=str(v.get("claim_id") or ""); m=INTERNAL_RE.fullmatch(raw)
        if m and m.group(1).upper()=="CLM":
            title,name=_ctx(v)
            if not (title or name):
                try:title,name=_ctx(core.REGISTRY.get_claim(raw))
                except (KeyError,ValueError):pass
            ident=IDENTITY.ensure_claim(int(m.group(2)),title,name); out.setdefault("public_id",ident["public_id"]); out.setdefault("slug",ident.get("slug"))
        return out
    return pubref(v,ctx) if isinstance(v,str) else v

def publicize_ref(value:str,context:dict[str,Any]|None=None)->str:
    return pubref(value,context)
def internalize_ref(value:str,expected_kind:str)->str:
    return internal(value,expected_kind)

def _validated_claim_payload(payload:dict[str,Any], *, fallback_declaration:str="", fill_title:bool=False) -> dict[str,Any]:
    result=dict(payload)
    declaration=str(result.get("formal_declaration") or result.get("declaration") or fallback_declaration or "").strip()
    _, inferred_name=validate_claim_declaration(declaration)
    supplied_name=str(result.get("lean_name") or "").strip()
    if supplied_name and supplied_name != inferred_name:
        raise ValueError(f"lean_name {supplied_name!r} does not match declaration name {inferred_name!r}")
    result["formal_declaration"]=declaration
    result["lean_name"]=inferred_name
    canonical=canonical_from_declaration(declaration)
    if not str(result.get("statement") or "").strip():result["statement"]=canonical["text"]
    if fill_title and not str(result.get("title") or "").strip():result["title"]=suggested_title(inferred_name)
    return result

def first(q:dict[str,list[str]],k:str,d:str="")->str:return q.get(k,[d])[0]
def iparam(q:dict[str,list[str]],k:str,d:int)->int:
    try:return int(first(q,k,str(d)))
    except ValueError as e:raise ValueError(f"{k} must be an integer") from e

def _graph_coverage(data:dict[str,Any])->dict[str,Any]:
    environment_hash=str(core.environment_metadata().get("environment_hash") or "")
    return COVERAGE.decorate_graph_clusters(data,environment_hash,["Mathlib"])
def graph_coverage_metadata(parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); raw=q.get("key",[])
    keys=list(dict.fromkeys(str(key) for key in raw if str(key).startswith("ns:")))[:100]
    environment_hash=str(core.environment_metadata().get("environment_hash") or "")
    return {"environment_hash":environment_hash,"coverages":COVERAGE.namespace_coverages(keys,environment_hash,["Mathlib"]),"returned":len(keys)}
def catalog(parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); cur=first(q,"cursor"); return CATALOG.page(limit=iparam(q,"limit",40),before_internal_id=iid(cur,"claim") if cur else None,query=first(q,"q"),status=first(q,"status","all"))
def _argument_data(cid:int,proof_limit:int=12)->dict[str,Any]:
    data=core.ARGUMENT.claim_argument(cid,proof_limit=proof_limit); data=COVERAGE.decorate_argument(data); data["formal_source"]=SOURCE_PROVENANCE.for_claim(cid,data.get("proofs") or []); return GRAPH_DISPLAY.decorate_argument(data)
def argument(ref:str,parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); return _argument_data(iid(ref,"claim"),iparam(q,"proof_limit",12))
def path_witness(ref:str,parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); target=first(q,"target"); via=first(q,"via")
    if not target:raise ValueError("target is required")
    return CATALOG.path_witness(iid(ref,"claim"),depth=iparam(q,"depth",2),target=target,via=via or None,route_through=q.get("route_through",[]))
def quantifier_witness(ref:str,parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query)
    return CATALOG.quantifier_witness(iid(ref,"claim"),not_uses=q.get("not_uses",[]),route_uses=q.get("route_uses",[]),route_not_uses=q.get("route_not_uses",[]))
def graph(parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); root=first(q,"claim"); data=core.ARGUMENT.neighborhood(root_internal_id=iid(root,"claim") if root else None,depth=iparam(q,"depth",1),limit=iparam(q,"limit",80),relation_mode=first(q,"relations","uses")); return GRAPH_DISPLAY.decorate(data)
def graph_world()->dict[str,Any]:return GRAPH_DISPLAY.decorate(_graph_coverage(WORLD.overview()))
def graph_cluster(parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); key=first(q,"key"); cur=first(q,"cursor")
    if not key:raise ValueError("cluster key is required")
    return GRAPH_DISPLAY.decorate(_graph_coverage(WORLD.cluster_slice(key,limit=iparam(q,"limit",100),before_internal_id=iid(cur,"claim") if cur else None)))
def graph_area(parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); area=first(q,"area"); cur=first(q,"cursor")
    if not area:raise ValueError("area is required")
    return GRAPH_DISPLAY.decorate(_graph_coverage(WORLD.area_slice(area,limit=iparam(q,"limit",100),before_internal_id=iid(cur,"claim") if cur else None)))
def claim(ref:str,parsed:Any)->dict[str,Any]:
    cid=iid(ref,"claim"); q=parse_qs(parsed.query); proof_limit=iparam(q,"proof_limit",12); h=HISTORY.claim_detail(cid,version_limit=iparam(q,"version_limit",8),proof_limit=proof_limit,builds_per_proof=iparam(q,"build_limit",3)); h["argument"]=_argument_data(cid,proof_limit); h["presentations"]=PRESENTATIONS.for_claim(cid); return h
def claim_presentations(ref:str,parsed:Any)->dict[str,Any]:
    q=parse_qs(parsed.query); return PRESENTATIONS.for_claim(iid(ref,"claim"),limit=iparam(q,"limit",24))
def proof(ref:str)->dict[str,Any]:
    pid=iid(ref,"proof"); x=HISTORY.proof_artifact(pid); arg=core.ARGUMENT.proof_route(pid); arg["coverage"]=COVERAGE.proof_coverage(arg); arg["formal_source"]=SOURCE_PROVENANCE.for_claim(iid(str(arg["claim_id"]),"claim"),[arg]); x["argument"]=arg; return x
def build(ref:str)->dict[str,Any]:
    bid=iid(ref,"build"); x=HISTORY.build_artifact(bid); pid=iid(x["proof_id"],"proof"); arg=core.ARGUMENT.proof_route(pid,bid); arg["coverage"]=COVERAGE.proof_coverage(arg); arg["formal_source"]=SOURCE_PROVENANCE.for_claim(iid(str(arg["claim_id"]),"claim"),[arg]); x["argument"]=arg; return x

def resolve_proof_coverage(ref:str,payload:dict[str,Any])->dict[str,Any]:
    pid=iid(ref,"proof"); route=core.ARGUMENT.proof_route(pid)
    if not route.get("argument_index_complete") or not route.get("argument_build_id"):
        return {"ok":False,"classified":0,"lean_invocations":0,"coverage":COVERAGE.proof_coverage(route),"message":"Proof has no complete argument witness"}
    argument_environment=COVERAGE._argument_environment(str(route["argument_build_id"]))
    runtime=core.runtime_status(); current_environment=str(runtime.get("environment_hash") or "")
    if not runtime.get("ready"):raise ValueError(str(runtime.get("message") or "Lean environment unavailable"))
    if not argument_environment or argument_environment!=current_environment:
        raise ValueError("coverage gaps belong to a different Lean environment; restore that environment before classifying them")
    imports=list(route.get("imports") or ["Mathlib"])
    claim_internal=iid(str(route["claim_id"]),"claim")
    raw=set(str(x) for x in (route.get("raw_refs") or {}).get("proof",[]) if str(x)); raw-=COVERAGE._self_constants(claim_internal,argument_environment)
    mapped=set()
    for dep in route.get("depends_on") or []:mapped.update(str(x) for x in dep.get("evidence_constants") or [] if str(x))
    remaining=sorted(raw-mapped); known=COVERAGE.kinds(argument_environment,imports,remaining); unknown=[name for name in remaining if name not in known]
    try:requested=int(payload.get("limit") or 200)
    except (TypeError,ValueError) as exc:raise ValueError("limit must be an integer") from exc
    limit=max(1,min(requested,200)); chunk=unknown[:limit]
    classifications={}; calls=duration=0; errors=[]
    if chunk:classifications,calls,duration,errors=dependency_import_module._classify_unbound(imports,chunk)
    coverage=COVERAGE.proof_coverage(route)
    return {"ok":not errors,"proof_id":route["proof_id"],"classified":len(classifications),"requested":len(chunk),"remaining_unclassified":int(coverage.get("unclassified_refs") or 0),"lean_invocations":calls,"lean_duration_ms":duration,"errors":errors[-5:],"coverage":coverage}

def import_task_action(task_id:str,action:str,payload:dict[str,Any]|None=None)->dict[str,Any]:
    task_id=str(task_id or "").upper(); action=str(action or "").lower()
    if not re.fullmatch(TASKISH,task_id,re.I):raise ValueError("invalid import task id")
    if action=="step":return TASKS.step(task_id)
    if action=="pause":return TASKS.pause(task_id)
    if action=="resume":return TASKS.resume(task_id)
    if action=="cancel":return TASKS.cancel(task_id)
    if action=="recover":return TASKS.recover(task_id)
    if action=="retry":return TASKS.retry_failed(task_id)
    if action=="extend":return TASKS.extend(task_id,payload or {})
    raise ValueError(f"unsupported import task action: {action}")

def import_claim(payload:dict[str,Any])->dict[str,Any]:
    imported=import_existing_declaration(payload)
    if not imported.get("already_registered"):return imported
    existing=core.REGISTRY.get_claim(str(imported["claim_id"])); existing["already_registered"]=True; existing["external_declaration"]=str(payload.get("declaration_name") or payload.get("lean_name") or payload.get("name") or ""); existing["external_binding"]=imported.get("external_binding"); return existing


def _lease_is_stale(task:dict[str,Any])->bool:
    raw=str(task.get("lease_started_at") or "").strip()
    if not raw:
        return bool(task.get("leased"))
    try:
        started=datetime.fromisoformat(raw.replace("Z","+00:00"))
        if started.tzinfo is None:
            started=started.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc)-started).total_seconds()>=IMPORT_LEASE_STALE_SECONDS
    except ValueError:
        return True


def _import_worker_loop()->None:
    """Advance active durable import tasks even when no browser is open."""
    while True:
        try:
            if core.runtime_status().get("ready"):
                tasks=(TASKS.list(limit=100) or {}).get("tasks") or []
                for task in tasks:
                    if task.get("status")!="active":
                        continue
                    task_id=str(task["task_id"])
                    if task.get("leased"):
                        if _lease_is_stale(task):
                            try:TASKS.recover(task_id)
                            except (KeyError,ValueError,sqlite3.Error):pass
                        else:
                            continue
                    try:
                        TASKS.step(task_id)
                    except (KeyError,ValueError,sqlite3.Error):
                        # step() already persists a safe paused/error state when it owns the lease.
                        pass
        except Exception:
            # Import work is best-effort background activity; HTTP service stays available.
            pass
        time.sleep(IMPORT_WORKER_INTERVAL_SECONDS)


def recover_persisted_import_leases()->int:
    """Clear leases left by a previous MathHub process before this worker starts.

    A freshly started process cannot own any persisted task lease yet, so every
    lease visible at this point belongs to a dead predecessor. Runtime stale
    detection remains conservative for leases acquired after startup.
    """
    recovered=0
    for task in (TASKS.list(limit=100) or {}).get("tasks") or []:
        if not task.get("leased"):
            continue
        task_id=str(task.get("task_id") or "")
        if not task_id:
            continue
        try:
            TASKS.recover(task_id)
            recovered+=1
        except (KeyError,ValueError,sqlite3.Error):
            pass
    return recovered


def start_import_worker()->bool:
    global _IMPORT_WORKER_STARTED
    if not AUTO_IMPORT_TASKS:
        return False
    with _IMPORT_WORKER_LOCK:
        if _IMPORT_WORKER_STARTED:
            return False
        _IMPORT_WORKER_STARTED=True
    recover_persisted_import_leases()
    threading.Thread(target=_import_worker_loop,name="mathhub-import-worker",daemon=True).start()
    return True


def _library_warmup_loop()->None:
    """Build the read-only Mathlib substrate once, retrying failed scans safely."""
    while True:
        runtime=core.runtime_status()
        if runtime.get("setup_error"):
            return
        if runtime.get("ready"):
            break
        time.sleep(LIBRARY_WARMUP_POLL_SECONDS)

    backoff=5.0
    while True:
        try:
            result=ensure_global_library_scan()
        except (ValueError,RuntimeError,sqlite3.Error) as exc:
            result={"ok":False,"error":str(exc)}
        if result.get("ok"):
            return
        # A failed full-environment scan is operator-visible through
        # /api/library/status. Retry with bounded backoff instead of silently
        # abandoning the library forever or hot-looping CPU.
        time.sleep(backoff)
        backoff=min(backoff*2.0,300.0)


def start_library_warmup()->bool:
    global _LIBRARY_WARMUP_STARTED
    if not AUTO_INDEX_NAMESPACES:
        return False
    with _LIBRARY_WARMUP_LOCK:
        if _LIBRARY_WARMUP_STARTED:
            return False
        _LIBRARY_WARMUP_STARTED=True
    threading.Thread(target=_library_warmup_loop,name="mathhub-library-warmup",daemon=True).start()
    return True


def _starter_task_matches(task:dict[str,Any])->bool:
    return set(str(name) for name in (task.get("roots") or []))==set(STARTER_CLAIM_ROOTS)


def starter_claim_status()->dict[str,Any]:
    runtime=core.runtime_status()
    current=CATALOG.page(limit=1,before_internal_id=None,query="",status="all")
    matching=[task for task in ((TASKS.list(limit=100) or {}).get("tasks") or []) if _starter_task_matches(task)]
    task=matching[0] if matching else None
    detail=None
    if task and task.get("task_id"):
        try:detail=TASKS.get(str(task["task_id"]))
        except (KeyError,ValueError,sqlite3.Error):detail=dict(task)
    lease_age_seconds=None
    if detail and detail.get("leased") and detail.get("lease_started_at"):
        try:
            started=datetime.fromisoformat(str(detail["lease_started_at"]).replace("Z","+00:00"))
            if started.tzinfo is None:
                started=started.replace(tzinfo=timezone.utc)
            lease_age_seconds=max(0,int((datetime.now(timezone.utc)-started).total_seconds()))
        except ValueError:
            lease_age_seconds=None
    return {
        "enabled":bool(AUTO_STARTER_CLAIMS and STARTER_CLAIM_ROOTS),
        "runtime_ready":bool(runtime.get("ready")),
        "registry_empty":not bool(current.get("claims")),
        "roots":list(STARTER_CLAIM_ROOTS),
        "task":detail,
        "lease_age_seconds":lease_age_seconds,
        "lease_stale_after_seconds":IMPORT_LEASE_STALE_SECONDS,
    }


def ensure_starter_claim_task()->str|None:
    """Ensure an empty registry has a live starter import for the current environment.

    A persisted task from an older Lean/Mathlib environment is not reusable.
    Paused tasks from transient failures are recovered/resumed instead of being
    mistaken for a completed bootstrap.
    """
    if not AUTO_STARTER_CLAIMS or not STARTER_CLAIM_ROOTS:
        return None
    runtime=core.runtime_status()
    if not runtime.get("ready"):
        return None
    current_environment_hash=str(runtime.get("environment_hash") or "")
    current=CATALOG.page(limit=1,before_internal_id=None,query="",status="all")
    if current.get("claims"):
        return None

    for summary in (TASKS.list(limit=100) or {}).get("tasks") or []:
        if not _starter_task_matches(summary):
            continue
        task_id=str(summary.get("task_id") or "")
        if not task_id:
            continue
        try:
            task=TASKS.get(task_id)
        except (KeyError,ValueError,sqlite3.Error):
            task=dict(summary)

        task_environment_hash=str(task.get("environment_hash") or "")
        if current_environment_hash and task_environment_hash and task_environment_hash != current_environment_hash:
            # Durable import tasks are environment-pinned. Leave historical task
            # state intact and create a fresh starter task below.
            continue

        status=str(task.get("status") or "")
        if status=="active":
            if task.get("leased") and _lease_is_stale(task):
                try:TASKS.recover(task_id)
                except (KeyError,ValueError,sqlite3.Error):pass
            return task_id

        if status=="paused":
            if task.get("leased"):
                try:TASKS.recover(task_id)
                except (KeyError,ValueError,sqlite3.Error):pass
            try:
                if int(task.get("failed") or 0)>0:
                    TASKS.retry_failed(task_id)
                refreshed=TASKS.get(task_id)
                if str(refreshed.get("status") or "")=="paused" and not refreshed.get("leased"):
                    TASKS.resume(task_id)
                return task_id
            except (KeyError,ValueError,sqlite3.Error):
                # If the persisted paused task cannot be recovered safely, keep
                # it as history and create a clean current-environment task.
                continue

        if status=="completed":
            if int(task.get("failed") or 0)>0:
                try:
                    retried=TASKS.retry_failed(task_id)
                    if str(retried.get("status") or "")=="active":
                        return task_id
                except (KeyError,ValueError,sqlite3.Error):
                    pass
            # A completed task that imported something should already have
            # populated the registry; keep it as the canonical bootstrap record.
            if int(task.get("imported") or 0)>0 or int(task.get("already_registered") or 0)>0:
                return task_id
            # A zero-result completed task is retained for diagnostics. Do not
            # create duplicates every poll; operator/UI can expose its detail.
            return task_id

        # cancelled/unknown tasks do not block a fresh starter import.

    task=TASKS.create({
        "declaration_names":list(STARTER_CLAIM_ROOTS),
        "imports":["Mathlib"],
        "max_depth":STARTER_CLAIM_MAX_DEPTH,
        "max_claims":STARTER_CLAIM_MAX_CLAIMS,
    })
    return str(task.get("task_id") or "") or None


def _starter_claim_bootstrap_loop()->None:
    """Keep an empty registry's starter import alive until Claims actually exist."""
    while True:
        try:
            if core.runtime_status().get("ready"):
                ensure_starter_claim_task()
                current=CATALOG.page(limit=1,before_internal_id=None,query="",status="all")
                if current.get("claims"):
                    return
        except (ValueError,RuntimeError,sqlite3.Error):
            # Runtime/setup and durable-task failures remain visible through
            # starter status; the next poll can recover transient pauses.
            pass
        time.sleep(LIBRARY_WARMUP_POLL_SECONDS)


def retire_legacy_starter_tasks()->int:
    """Cancel persisted automatic starter tasks now that library browsing is read-only.

    Exact starter-root tasks created by older MathHub versions must not keep the
    import worker busy after this migration.
    """
    if AUTO_STARTER_CLAIMS:
        return 0
    retired=0
    for task in (TASKS.list(limit=100) or {}).get("tasks") or []:
        if not _starter_task_matches(task):
            continue
        if str(task.get("status") or "") not in {"active","paused"}:
            continue
        task_id=str(task.get("task_id") or "")
        if not task_id:
            continue
        try:
            if task.get("leased"):
                TASKS.recover(task_id)
            TASKS.cancel(task_id)
            retired+=1
        except (KeyError,ValueError,sqlite3.Error):
            pass
    return retired


def start_starter_claim_bootstrap()->bool:
    global _STARTER_BOOTSTRAP_STARTED
    if not AUTO_STARTER_CLAIMS or not STARTER_CLAIM_ROOTS:
        return False
    with _STARTER_BOOTSTRAP_LOCK:
        if _STARTER_BOOTSTRAP_STARTED:
            return False
        _STARTER_BOOTSTRAP_STARTED=True
    threading.Thread(target=_starter_claim_bootstrap_loop,name="mathhub-starter-claims",daemon=True).start()
    return True


class Handler(core.Handler):
    def read_source_ingest_json(self)->dict[str,Any]:
        try:size=int(self.headers.get("Content-Length","0"))
        except ValueError as exc:raise ValueError("invalid request size") from exc
        if size<1 or size>2_000_000:raise ValueError("source ingest request must be between 1 byte and 2 MB")
        payload=json.loads(self.rfile.read(size).decode("utf-8"))
        if not isinstance(payload,dict):raise ValueError("JSON object expected")
        return payload
    def do_GET(self)->None:
        p=urlparse(self.path); path=p.path
        try:
            if path=="/api/claims":self.json_response(200,publicize(catalog(p)));return
            if path=="/api/import-tasks":self.json_response(200,publicize(TASKS.list(limit=iparam(parse_qs(p.query),"limit",24))));return
            if path=="/api/library/status":self.json_response(200,global_library_status());return
            if path=="/api/library/world":self.json_response(200,global_library_world());return
            if path=="/api/library/cluster":
                q=parse_qs(p.query);ns=str((q.get("namespace") or [""])[0]);limit=iparam(q,"limit",100)
                self.json_response(200,global_library_cluster(ns,limit=limit));return
            if path=="/api/library/module":
                q=parse_qs(p.query);module=str((q.get("path") or [""])[0]);limit=iparam(q,"limit",36)
                self.json_response(200,global_library_module(module,limit=limit));return
            if path=="/api/library/theorem":
                q=parse_qs(p.query);name=str((q.get("name") or [""])[0]);refresh=first(q,"refresh","0") in {"1","true","yes"}
                self.json_response(200,publicize(library_theorem_detail(name,refresh=refresh)));return
            if path=="/api/library/dependencies":
                q=parse_qs(p.query);name=str((q.get("name") or [""])[0]);refresh=first(q,"refresh","0") in {"1","true","yes"}
                self.json_response(200,publicize(library_declaration_dependencies(name,refresh=refresh)));return
            if path=="/api/library/random":
                self.json_response(200,random_library_theorem());return
            if path=="/api/starter-status":self.json_response(200,publicize(starter_claim_status()));return
            m=re.fullmatch(rf"/api/import-tasks/({TASKISH})",path,re.I)
            if m:self.json_response(200,publicize(TASKS.get(m.group(1).upper())));return
            if path=="/api/graph/coverage":self.json_response(200,publicize(graph_coverage_metadata(p)));return
            if path=="/api/graph/world":self.json_response(200,publicize(graph_world()));return
            if path=="/api/graph/cluster":self.json_response(200,publicize(graph_cluster(p)));return
            if path=="/api/graph/area":self.json_response(200,publicize(graph_area(p)));return
            if path=="/api/graph":self.json_response(200,publicize(graph(p)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/quantifier-witness",path,re.I)
            if m:self.json_response(200,publicize(quantifier_witness(m.group(1),p)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/path-witness",path,re.I)
            if m:self.json_response(200,publicize(path_witness(m.group(1),p)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/argument",path,re.I)
            if m:self.json_response(200,publicize(argument(m.group(1),p)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/presentations",path,re.I)
            if m:self.json_response(200,publicize(claim_presentations(m.group(1),p)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})",path,re.I)
            if m:self.json_response(200,publicize(claim(m.group(1),p)));return
            m=re.fullmatch(rf"/api/proofs/({PUBLICISH})",path,re.I)
            if m:self.json_response(200,publicize(proof(m.group(1))));return
            m=re.fullmatch(rf"/api/builds/({PUBLICISH})",path,re.I)
            if m:self.json_response(200,publicize(build(m.group(1))));return
        except KeyError:self.json_response(404,{"error":"object not found"});return
        except ValueError as e:self.json_response(400,{"error":str(e)});return
        super().do_GET()
    def do_POST(self)->None:
        path=urlparse(self.path).path
        try:
            if path=="/api/source-ingest":self.json_response(201,ingest_source(self.read_source_ingest_json(),data_root=core.DATA_ROOT));return
            if path=="/api/library/namespace":self.json_response(200,publicize(list_namespace_declarations(self.read_json())));return
            if path=="/api/import-tasks":self.json_response(201,publicize(TASKS.create(self.read_json())));return
            m=re.fullmatch(rf"/api/import-tasks/({TASKISH})/(step|pause|resume|cancel|recover|retry|extend)",path,re.I)
            if m:
                payload=self.read_json() if m.group(2).lower()=="extend" else None
                self.json_response(200,publicize(import_task_action(m.group(1),m.group(2),payload)));return
            m=re.fullmatch(rf"/api/proofs/({PUBLICISH})/coverage/resolve",path,re.I)
            if m:self.json_response(200,publicize(resolve_proof_coverage(m.group(1),self.read_json())));return
            if path=="/api/claims/import-closure":self.json_response(200,publicize(import_dependency_closure(self.read_json())));return
            if path=="/api/claims/import-batch":self.json_response(200,publicize(import_existing_declarations(self.read_json())));return
            if path=="/api/claims/import":
                data=import_claim(self.read_json()); self.json_response(200 if data.get("already_registered") else 201,publicize(data));return
            if path=="/api/claims":
                payload=_validated_claim_payload(self.read_json(),fill_title=True); self.json_response(201,publicize(core.register_claim(payload)));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/presentations",path,re.I)
            if m:data=PRESENTATIONS.add_curated(iid(m.group(1),"claim"),self.read_json()); self.json_response(201,publicize(data));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/proofs",path,re.I)
            if m:
                cid=internal(m.group(1),"claim"); p=self.read_json(); x=core.REGISTRY.create_proof(cid,p)
                if p.get("build") and core.runtime_status()["ready"]:x["build"]=core.build_registered_proof(x["proof_id"])
                self.json_response(201,publicize(x));return
            m=re.fullmatch(rf"/api/proofs/({PUBLICISH})/build",path,re.I)
            if m:self.json_response(201,publicize(core.build_registered_proof(internal(m.group(1),"proof"))));return
            m=re.fullmatch(rf"/api/claims/({PUBLICISH})/versions",path,re.I)
            if m:
                claim_ref=internal(m.group(1),"claim"); payload=self.read_json()
                if "formal_declaration" in payload or "declaration" in payload:payload=_validated_claim_payload(payload)
                self.json_response(201,publicize(core.REGISTRY.create_version(claim_ref,payload)));return
            super().do_POST()
        except KeyError as e:self.json_response(404,{"error":f"not found: {e}"})
        except (ValueError,json.JSONDecodeError,sqlite3.Error) as e:self.json_response(400,{"error":str(e),"ok":False})

if __name__=="__main__":
    core.request_runtime_setup()
    retire_legacy_starter_tasks()
    start_import_worker()
    start_library_warmup()
    print(f"MathHub: http://{core.HOST}:{core.PORT}"); print(core.runtime_status()["message"]); print("Ontology: Claim / Proof / ProofDependency."); ThreadingHTTPServer((core.HOST,core.PORT),Handler).serve_forever()
