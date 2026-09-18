from __future__ import annotations

"""Canonical owner of the historical append-oriented project graph store.

Physical ``witness_*`` SQLite identifiers and ``witness-core-v0.1`` serialized
snapshots remain readable for compatibility. New graph identities and public
Python types are Testamur-owned.
"""

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import EdgeKind, ObjectKind, SCHEMA_VERSION, canonical_hash, canonical_json, new_id, normalize_edge_kind, normalize_object_kind, normalize_verification_status, validate_slug


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TestamurStore:
    """Append-oriented Testamur graph store over a legacy-compatible schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15); conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA journal_mode=WAL"); return conn

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS witness_projects(id TEXT PRIMARY KEY,slug TEXT NOT NULL UNIQUE,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS witness_objects(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES witness_projects(id),kind TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS witness_revisions(id TEXT PRIMARY KEY,object_id TEXT NOT NULL REFERENCES witness_objects(id),revision_no INTEGER NOT NULL,payload_json TEXT NOT NULL,content_hash TEXT NOT NULL,actor_ref TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(object_id,revision_no));
        CREATE TABLE IF NOT EXISTS witness_edges(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES witness_projects(id),source_object_id TEXT NOT NULL REFERENCES witness_objects(id),target_object_id TEXT NOT NULL REFERENCES witness_objects(id),kind TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,UNIQUE(project_id,source_object_id,target_object_id,kind,payload_json));
        CREATE TABLE IF NOT EXISTS witness_verification_runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES witness_projects(id),claim_object_id TEXT NOT NULL REFERENCES witness_objects(id),verifier_object_id TEXT NOT NULL REFERENCES witness_objects(id),status TEXT NOT NULL,domain_status TEXT NOT NULL DEFAULT '',input_revisions_json TEXT NOT NULL,environment_json TEXT NOT NULL,result_json TEXT NOT NULL,artifact_ids_json TEXT NOT NULL,actor_ref TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS witness_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES witness_projects(id),event_kind TEXT NOT NULL,subject_id TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_witness_objects_project_kind ON witness_objects(project_id,kind);
        CREATE INDEX IF NOT EXISTS idx_witness_revisions_object ON witness_revisions(object_id,revision_no DESC);
        CREATE INDEX IF NOT EXISTS idx_witness_edges_source ON witness_edges(project_id,source_object_id,kind);
        CREATE INDEX IF NOT EXISTS idx_witness_edges_target ON witness_edges(project_id,target_object_id,kind);
        CREATE INDEX IF NOT EXISTS idx_witness_runs_claim ON witness_verification_runs(project_id,claim_object_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_witness_events_project ON witness_events(project_id,seq);
        CREATE TRIGGER IF NOT EXISTS witness_revisions_no_update BEFORE UPDATE ON witness_revisions BEGIN SELECT RAISE(ABORT,'Testamur revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS witness_revisions_no_delete BEFORE DELETE ON witness_revisions BEGIN SELECT RAISE(ABORT,'Testamur revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS witness_edges_no_update BEFORE UPDATE ON witness_edges BEGIN SELECT RAISE(ABORT,'Testamur edges are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS witness_edges_no_delete BEFORE DELETE ON witness_edges BEGIN SELECT RAISE(ABORT,'Testamur edges are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS witness_runs_no_update BEFORE UPDATE ON witness_verification_runs BEGIN SELECT RAISE(ABORT,'Testamur verification runs are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS witness_runs_no_delete BEFORE DELETE ON witness_verification_runs BEGIN SELECT RAISE(ABORT,'Testamur verification runs are immutable'); END;
        """
        with self.connect() as conn: conn.executescript(schema)

    @staticmethod
    def _json(value: Any) -> str: return canonical_json(value)
    @staticmethod
    def _load(value: str) -> Any: return json.loads(value)

    def _event(self, conn: sqlite3.Connection, project_id: str, event_kind: str, subject_id: str, payload: dict[str, Any] | None = None) -> int:
        cur=conn.execute("INSERT INTO witness_events(project_id,event_kind,subject_id,payload_json,created_at) VALUES(?,?,?,?,?)",(project_id,event_kind,subject_id,self._json(payload or {}),utc_now())); return int(cur.lastrowid)

    def create_project(self, slug: str, name: str, description: str = "") -> dict[str, Any]:
        slug=validate_slug(slug); name=str(name or "").strip()
        if not name: raise ValueError("project name is required")
        project_id=new_id("tpr"); now=utc_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO witness_projects(id,slug,name,description,created_at) VALUES(?,?,?,?,?)",(project_id,slug,name,str(description or "").strip(),now)); self._event(conn,project_id,"project_created",project_id,{"slug":slug,"name":name})
        return self.get_project(project_id)

    def get_project(self, project_id_or_slug: str) -> dict[str, Any]:
        with self.connect() as conn: row=conn.execute("SELECT * FROM witness_projects WHERE id=? OR slug=?",(project_id_or_slug,str(project_id_or_slug).lower())).fetchone()
        if not row: raise KeyError(project_id_or_slug)
        return dict(row)

    def _project_id(self,value:str)->str: return str(self.get_project(value)["id"])
    @staticmethod
    def _object_project(conn:sqlite3.Connection,object_id:str)->str:
        row=conn.execute("SELECT project_id FROM witness_objects WHERE id=?",(object_id,)).fetchone()
        if not row: raise KeyError(object_id)
        return str(row["project_id"])
    @staticmethod
    def _object_kind(conn:sqlite3.Connection,object_id:str)->str:
        row=conn.execute("SELECT kind FROM witness_objects WHERE id=?",(object_id,)).fetchone()
        if not row: raise KeyError(object_id)
        return str(row["kind"])
    @staticmethod
    def _latest_revision_row(conn:sqlite3.Connection,object_id:str)->sqlite3.Row:
        row=conn.execute("SELECT * FROM witness_revisions WHERE object_id=? ORDER BY revision_no DESC LIMIT 1",(object_id,)).fetchone()
        if not row: raise KeyError(object_id)
        return row

    def create_object(self,project:str,kind:str|ObjectKind,payload:dict[str,Any],*,actor_ref:str="")->dict[str,Any]:
        project_id=self._project_id(project); kind_value=normalize_object_kind(kind)
        if not isinstance(payload,dict) or not payload: raise ValueError("Testamur object payload must be a non-empty object")
        object_id=new_id("tob"); revision_id=new_id("trv"); now=utc_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO witness_objects(id,project_id,kind,created_at) VALUES(?,?,?,?)",(object_id,project_id,kind_value,now)); conn.execute("INSERT INTO witness_revisions(id,object_id,revision_no,payload_json,content_hash,actor_ref,created_at) VALUES(?,?,?,?,?,?,?)",(revision_id,object_id,1,self._json(payload),canonical_hash(payload),actor_ref,now)); self._event(conn,project_id,"object_created",object_id,{"kind":kind_value,"revision_id":revision_id})
        return self.get_object(object_id)

    def revise_object(self,object_id:str,payload:dict[str,Any],*,actor_ref:str="")->dict[str,Any]:
        if not isinstance(payload,dict) or not payload: raise ValueError("Testamur revision payload must be a non-empty object")
        now=utc_now(); revision_id=new_id("trv")
        with self.connect() as conn:
            project_id=self._object_project(conn,object_id); previous=self._latest_revision_row(conn,object_id); revision_no=int(previous["revision_no"])+1
            conn.execute("INSERT INTO witness_revisions(id,object_id,revision_no,payload_json,content_hash,actor_ref,created_at) VALUES(?,?,?,?,?,?,?)",(revision_id,object_id,revision_no,self._json(payload),canonical_hash(payload),actor_ref,now)); self._event(conn,project_id,"object_revised",object_id,{"revision_id":revision_id,"revision_no":revision_no,"previous_revision_id":previous["id"]})
        return self.get_object(object_id)

    def get_object(self,object_id:str)->dict[str,Any]:
        with self.connect() as conn:
            obj=conn.execute("SELECT * FROM witness_objects WHERE id=?",(object_id,)).fetchone()
            if not obj: raise KeyError(object_id)
            rev=self._latest_revision_row(conn,object_id)
        return {"id":obj["id"],"project_id":obj["project_id"],"kind":obj["kind"],"created_at":obj["created_at"],"revision_id":rev["id"],"revision_no":int(rev["revision_no"]),"content_hash":rev["content_hash"],"actor_ref":rev["actor_ref"],"revised_at":rev["created_at"],"payload":self._load(rev["payload_json"])}

    def list_objects(self,project:str,kind:str|ObjectKind|None=None)->list[dict[str,Any]]:
        project_id=self._project_id(project); params:list[Any]=[project_id]; where="WHERE project_id=?"
        if kind is not None: where+=" AND kind=?"; params.append(normalize_object_kind(kind))
        with self.connect() as conn: rows=conn.execute(f"SELECT id FROM witness_objects {where} ORDER BY created_at,id",params).fetchall()
        return [self.get_object(str(row["id"])) for row in rows]

    def add_edge(self,project:str,source_object_id:str,target_object_id:str,kind:str|EdgeKind,payload:dict[str,Any]|None=None)->dict[str,Any]:
        project_id=self._project_id(project); edge_kind=normalize_edge_kind(kind); edge_id=new_id("ted"); payload=payload or {}; now=utc_now()
        with self.connect() as conn:
            for object_id in (source_object_id,target_object_id):
                if self._object_project(conn,object_id)!=project_id: raise ValueError("Testamur edges cannot cross projects")
            try: conn.execute("INSERT INTO witness_edges(id,project_id,source_object_id,target_object_id,kind,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",(edge_id,project_id,source_object_id,target_object_id,edge_kind,self._json(payload),now))
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" not in str(exc).upper(): raise
                row=conn.execute("SELECT * FROM witness_edges WHERE project_id=? AND source_object_id=? AND target_object_id=? AND kind=? AND payload_json=?",(project_id,source_object_id,target_object_id,edge_kind,self._json(payload))).fetchone()
                if row: return self._serialize_edge(row)
                raise
            self._event(conn,project_id,"edge_added",edge_id,{"source":source_object_id,"target":target_object_id,"kind":edge_kind})
        return {"id":edge_id,"project_id":project_id,"source_object_id":source_object_id,"target_object_id":target_object_id,"kind":edge_kind,"payload":payload,"created_at":now}

    def _serialize_edge(self,row:sqlite3.Row)->dict[str,Any]: return {"id":row["id"],"project_id":row["project_id"],"source_object_id":row["source_object_id"],"target_object_id":row["target_object_id"],"kind":row["kind"],"payload":self._load(row["payload_json"]),"created_at":row["created_at"]}
    def list_edges(self,project:str)->list[dict[str,Any]]:
        project_id=self._project_id(project)
        with self.connect() as conn: rows=conn.execute("SELECT * FROM witness_edges WHERE project_id=? ORDER BY created_at,id",(project_id,)).fetchall()
        return [self._serialize_edge(row) for row in rows]

    def _pin_revisions(self,conn:sqlite3.Connection,project_id:str,object_ids:Iterable[str])->dict[str,dict[str,Any]]:
        pinned={}
        for object_id in dict.fromkeys(str(item) for item in object_ids):
            if self._object_project(conn,object_id)!=project_id: raise ValueError("verification inputs cannot cross projects")
            row=self._latest_revision_row(conn,object_id); pinned[object_id]={"revision_id":row["id"],"revision_no":int(row["revision_no"]),"content_hash":row["content_hash"]}
        return pinned

    def record_verification(self,project:str,claim_object_id:str,verifier_object_id:str,status:str,*,input_object_ids:Iterable[str]=(),domain_status:str="",environment:dict[str,Any]|None=None,result:dict[str,Any]|None=None,artifact_ids:Iterable[str]=(),actor_ref:str="")->dict[str,Any]:
        project_id=self._project_id(project); status_value=normalize_verification_status(status); run_id=new_id("tvr"); now=utc_now()
        with self.connect() as conn:
            if self._object_project(conn,claim_object_id)!=project_id: raise ValueError("claim is not part of this project")
            if self._object_kind(conn,claim_object_id)!=ObjectKind.CLAIM.value: raise ValueError("verification target must be a Claim")
            if self._object_project(conn,verifier_object_id)!=project_id: raise ValueError("verifier is not part of this project")
            if self._object_kind(conn,verifier_object_id)!=ObjectKind.VERIFIER.value: raise ValueError("verification requires a Verifier object")
            input_ids=list(dict.fromkeys([claim_object_id,*map(str,input_object_ids)])); pinned=self._pin_revisions(conn,project_id,input_ids); artifacts=list(dict.fromkeys(map(str,artifact_ids)))
            for artifact_id in artifacts:
                if self._object_project(conn,artifact_id)!=project_id: raise ValueError("verification artifacts cannot cross projects")
            conn.execute("INSERT INTO witness_verification_runs(id,project_id,claim_object_id,verifier_object_id,status,domain_status,input_revisions_json,environment_json,result_json,artifact_ids_json,actor_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(run_id,project_id,claim_object_id,verifier_object_id,status_value,str(domain_status or "").strip(),self._json(pinned),self._json(environment or {}),self._json(result or {}),self._json(artifacts),actor_ref,now)); self._event(conn,project_id,"verification_recorded",run_id,{"claim_id":claim_object_id,"status":status_value})
        return self.get_verification_run(run_id)

    def _run_is_stale(self,conn:sqlite3.Connection,row:sqlite3.Row)->tuple[bool,list[str]]:
        pinned=self._load(row["input_revisions_json"]); stale_inputs=[]
        for object_id,snapshot in pinned.items():
            current=self._latest_revision_row(conn,object_id)
            if current["id"]!=snapshot.get("revision_id"): stale_inputs.append(object_id)
        return bool(stale_inputs),stale_inputs

    def _serialize_run(self,conn:sqlite3.Connection,row:sqlite3.Row)->dict[str,Any]:
        stale,stale_inputs=self._run_is_stale(conn,row); return {"id":row["id"],"project_id":row["project_id"],"claim_object_id":row["claim_object_id"],"verifier_object_id":row["verifier_object_id"],"status":row["status"],"domain_status":row["domain_status"],"stale":stale,"stale_inputs":stale_inputs,"input_revisions":self._load(row["input_revisions_json"]),"environment":self._load(row["environment_json"]),"result":self._load(row["result_json"]),"artifact_ids":self._load(row["artifact_ids_json"]),"actor_ref":row["actor_ref"],"created_at":row["created_at"]}
    def get_verification_run(self,run_id:str)->dict[str,Any]:
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM witness_verification_runs WHERE id=?",(run_id,)).fetchone()
            if not row: raise KeyError(run_id)
            return self._serialize_run(conn,row)
    def list_verification_runs(self,project:str)->list[dict[str,Any]]:
        project_id=self._project_id(project)
        with self.connect() as conn: rows=conn.execute("SELECT * FROM witness_verification_runs WHERE project_id=? ORDER BY created_at,id",(project_id,)).fetchall(); return [self._serialize_run(conn,row) for row in rows]
    def _latest_claim_run(self,conn:sqlite3.Connection,project_id:str,claim_id:str)->dict[str,Any]|None:
        row=conn.execute("SELECT * FROM witness_verification_runs WHERE project_id=? AND claim_object_id=? ORDER BY rowid DESC LIMIT 1",(project_id,claim_id)).fetchone(); return self._serialize_run(conn,row) if row else None

    def project_state(self,project:str,*,recent_limit:int=20)->dict[str,Any]:
        project_row=self.get_project(project); project_id=str(project_row["id"]); objects=self.list_objects(project_id); by_kind:dict[str,list[dict[str,Any]]]=defaultdict(list)
        for item in objects: by_kind[item["kind"]].append(item)
        with self.connect() as conn:
            claim_states=[]; status_counts:Counter[str]=Counter()
            for claim in by_kind[ObjectKind.CLAIM.value]:
                run=self._latest_claim_run(conn,project_id,claim["id"]); effective="unverified" if run is None else "stale" if run["stale"] else str(run["status"]); status_counts[effective]+=1; claim_states.append({"claim":claim,"effective_status":effective,"latest_run":run})
            events=conn.execute("SELECT * FROM witness_events WHERE project_id=? ORDER BY seq DESC LIMIT ?",(project_id,max(0,int(recent_limit)))).fetchall()
        open_claims=[item for item in claim_states if item["effective_status"] in {"unverified","stale","contradicted"}]
        return {"schema_version":SCHEMA_VERSION,"project":project_row,"object_counts":dict(Counter(item["kind"] for item in objects)),"verification_summary":dict(status_counts),"claims":claim_states,"open_claims":open_claims,"assumptions":by_kind[ObjectKind.ASSUMPTION.value],"active_risks":[*by_kind[ObjectKind.COUNTEREVIDENCE.value],*by_kind[ObjectKind.UNCERTAINTY.value]],"recent_events":[{"seq":int(row["seq"]),"event_kind":row["event_kind"],"subject_id":row["subject_id"],"payload":self._load(row["payload_json"]),"created_at":row["created_at"]} for row in events]}

    def epistemic_diff(self,project:str,from_seq:int,to_seq:int|None=None)->dict[str,Any]:
        project_id=self._project_id(project); upper_clause="" if to_seq is None else " AND seq<=?"; params:list[Any]=[project_id,int(from_seq)]
        if to_seq is not None: params.append(int(to_seq))
        with self.connect() as conn:
            rows=conn.execute(f"SELECT * FROM witness_events WHERE project_id=? AND seq>?{upper_clause} ORDER BY seq",params).fetchall(); changed_objects={str(row["subject_id"]) for row in rows if row["event_kind"] in {"object_created","object_revised"}}; revised_objects={str(row["subject_id"]) for row in rows if row["event_kind"]=="object_revised"}; potentially_invalidated:set[str]=set(); frontier=list(revised_objects); seen=set(frontier)
            while frontier:
                target=frontier.pop(); dependents=conn.execute("SELECT source_object_id FROM witness_edges WHERE project_id=? AND target_object_id=? AND kind IN('depends_on','assumes','derived_from')",(project_id,target)).fetchall()
                for dep in dependents:
                    source=str(dep["source_object_id"])
                    if self._object_kind(conn,source)==ObjectKind.CLAIM.value: potentially_invalidated.add(source)
                    if source not in seen: seen.add(source); frontier.append(source)
        event_counts=Counter(str(row["event_kind"]) for row in rows); return {"schema_version":SCHEMA_VERSION,"project_id":project_id,"from_seq_exclusive":int(from_seq),"to_seq_inclusive":to_seq,"event_count":len(rows),"event_counts":dict(event_counts),"changed_objects":sorted(changed_objects),"revised_objects":sorted(revised_objects),"potentially_invalidated_claims":sorted(potentially_invalidated),"events":[{"seq":int(row["seq"]),"event_kind":row["event_kind"],"subject_id":row["subject_id"],"payload":self._load(row["payload_json"]),"created_at":row["created_at"]} for row in rows]}

    def export_project(self,project:str)->dict[str,Any]:
        project_row=self.get_project(project); project_id=str(project_row["id"]); objects=self.list_objects(project_id)
        with self.connect() as conn:
            revisions=conn.execute("SELECT r.* FROM witness_revisions r JOIN witness_objects o ON o.id=r.object_id WHERE o.project_id=? ORDER BY r.object_id,r.revision_no",(project_id,)).fetchall(); events=conn.execute("SELECT * FROM witness_events WHERE project_id=? ORDER BY seq",(project_id,)).fetchall()
        return {"schema_version":SCHEMA_VERSION,"project":project_row,"objects":objects,"revisions":[{"id":row["id"],"object_id":row["object_id"],"revision_no":int(row["revision_no"]),"payload":self._load(row["payload_json"]),"content_hash":row["content_hash"],"actor_ref":row["actor_ref"],"created_at":row["created_at"]} for row in revisions],"edges":self.list_edges(project_id),"verification_runs":self.list_verification_runs(project_id),"events":[{"seq":int(row["seq"]),"event_kind":row["event_kind"],"subject_id":row["subject_id"],"payload":self._load(row["payload_json"]),"created_at":row["created_at"]} for row in events]}


LegacyGraphStore = TestamurStore
__all__ = ["LegacyGraphStore", "TestamurStore", "utc_now"]
