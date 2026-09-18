from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ._work_session_types import WorkSessionStatus
from .agent_capture import AgentCapture
from .blob_store import TestamurBlobStore
from .runtime_protocol import canonical_hash
from .source_fetch import SourceFetchPolicy, fetch_source
from .source_store import TestamurSourceStore
from .watch_store import TestamurWatchStore
from .work_session import WorkSessionStore


@dataclass(frozen=True, slots=True)
class GatewayCapture:
    source: dict[str, Any]
    snapshot: dict[str, Any]
    revision: dict[str, Any] | None
    blob: dict[str, Any] | None
    content: bytes | None
    event_receipt: dict[str, Any] | None

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": "testamur.source-gateway.capture.v1",
            "source": self.source,
            "snapshot": self.snapshot,
            "revision": self.revision,
            "blob": self.blob,
            "event_receipt": self.event_receipt,
            "content_returned_separately": self.content is not None,
            "semantics": {
                "exact_revision_captured": self.revision is not None,
                "durable_reliance_implied": False,
                "truth_implied": False,
                "publication_rights_implied": False,
                "work_session_binding_is_observation_only": True,
            },
        }


class TestamurSourceGateway:
    """Local exact-revision mediation layer for agent source access.

    Acquisition owns exact bytes, CAS retention, Source/Snapshot/SourceRevision
    creation, and optional canonical WorkSession observation. It never promotes a
    fetch into durable reliance; reconciliation remains the only promotion path.
    """

    def __init__(self, database_path: str | Path, *, blob_root: str | Path | None = None) -> None:
        self.path = Path(database_path)
        self.sources = TestamurSourceStore(self.path)
        self.blobs = TestamurBlobStore(
            Path(blob_root) if blob_root is not None else self.path.parent / "blobs"
        )
        self.sessions = WorkSessionStore(self.path)
        self.capture = AgentCapture(self.sessions)
        self.watches = TestamurWatchStore(self.path)

    def close(self) -> None:
        self.sessions.close()

    def __enter__(self) -> "TestamurSourceGateway":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _require_open_session(self, session_id: str) -> str:
        resolved = str(session_id).strip()
        if not resolved:
            raise ValueError("session_id must not be empty")
        session = self.sessions.get_session(resolved)
        if session.status is not WorkSessionStatus.OPEN:
            raise ValueError("Source Gateway binding requires an OPEN WorkSession")
        return resolved

    def fetch(
        self,
        locator: str,
        *,
        session_id: str | None = None,
        actor: Mapping[str, Any] | None = None,
        policy: SourceFetchPolicy | None = None,
    ) -> GatewayCapture:
        resolved_session = None if session_id is None else self._require_open_session(session_id)

        # Policy rejection and invalid session binding fail before any network or
        # durable source side effect. Operational retrieval failure is recordable
        # as an unavailable Snapshot, but never fabricates a SourceRevision.
        fetched = fetch_source(locator, policy=policy)
        source = self.sources.get_or_create_source(locator)
        metadata = dict(fetched.metadata)
        metadata["access_method"] = "testamur_source_gateway"
        metadata["gateway_exact_content_attempted"] = True
        metadata["gateway_actor"] = dict(actor or {"ref": "testamur:source-gateway"})

        blob = None
        if fetched.content is not None:
            blob = self.blobs.put(fetched.content)
            metadata["blob"] = {
                **blob,
                "publication_rights_implied": False,
            }

        snapshot = self.sources.record_snapshot(
            str(source["source_id"]),
            locator=fetched.final_locator,
            content=fetched.content,
            status=fetched.status,
            retrieval_metadata=metadata,
        )
        if blob is not None and snapshot.get("content_hash") != blob.get("content_hash"):
            raise RuntimeError("gateway Snapshot digest does not match persisted blob digest")

        revision = None
        if snapshot.get("revision_id") is not None:
            revision = self.sources.get_revision(str(snapshot["revision_id"]))
            if revision is None:
                raise RuntimeError("gateway created Snapshot with missing SourceRevision")

        receipt = None
        if resolved_session is not None:
            receipt = self._bind_capture(
                session_id=resolved_session,
                source=source,
                snapshot=snapshot,
                revision=revision,
                actor=actor,
            )

        return GatewayCapture(
            source=source,
            snapshot=snapshot,
            revision=revision,
            blob=blob,
            content=fetched.content,
            event_receipt=receipt,
        )

    def _candidate_for_source(
        self,
        session_id: str,
        source: Mapping[str, Any],
        *,
        observed_at: str,
    ):
        locator = str(source["initial_locator"])
        for candidate in self.sessions.list_candidates(session_id):
            if candidate.locator == locator:
                return candidate
        return self.capture.discover(
            session_id,
            locator=locator,
            metadata={
                "source_id": str(source["source_id"]),
                "access_method": "testamur_source_gateway",
                "gateway_managed": True,
            },
            observed_at=observed_at,
        )

    def _bind_capture(
        self,
        *,
        session_id: str,
        source: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        revision: Mapping[str, Any] | None,
        actor: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        # Re-check because callers may use this private method in tests or future
        # adapters. The public fetch path checks before network access.
        self._require_open_session(session_id)
        candidate = self._candidate_for_source(
            session_id,
            source,
            observed_at=str(snapshot["observed_at"]),
        )
        base = {
            "schema": "testamur.source-gateway.work-session-binding.v1",
            "candidate_id": candidate.candidate_id,
            "snapshot_id": str(snapshot["snapshot_id"]),
            "source_revision_id": None if revision is None else str(revision["revision_id"]),
            "actor": dict(actor or {"ref": "testamur:source-gateway"}),
            "durable_reliance_implied": False,
        }
        if revision is None:
            return {
                **base,
                "retrieval_receipt_ref": None,
                "observation": None,
                "binding_state": "DISCOVERED_ONLY",
            }

        retrieval_ref = "tst:retrieval:" + canonical_hash(
            {
                "session_id": session_id,
                "candidate_id": candidate.candidate_id,
                "snapshot_id": snapshot["snapshot_id"],
                "source_revision_id": revision["revision_id"],
                "actor": base["actor"],
            }
        )
        observation = self.capture.fetched(
            session_id,
            candidate.candidate_id,
            source_revision_id=str(revision["revision_id"]),
            retrieval_receipt_ref=retrieval_ref,
            created_at=str(snapshot["observed_at"]),
        )
        payload = asdict(observation)
        payload["usage_state"] = observation.usage_state.value
        payload["evidence_class"] = observation.evidence_class.value
        payload["evidence_refs"] = list(observation.evidence_refs)
        return {
            **base,
            "retrieval_receipt_ref": retrieval_ref,
            "observation": payload,
            "binding_state": "FETCHED",
        }

    def open_revision(self, revision_id: str, *, max_bytes: int | None = None) -> bytes:
        revision = self.sources.get_revision(str(revision_id))
        if revision is None:
            raise KeyError(revision_id)
        return self.blobs.get(str(revision["content_hash"]), max_bytes=max_bytes)

    def source_status(self, source_id: str) -> dict[str, Any]:
        source = self.sources.get_source(str(source_id))
        if source is None:
            raise KeyError(source_id)
        latest = self.sources.latest_recorded_snapshot(str(source_id))
        revision = None
        blob = None
        if latest is not None and latest.get("revision_id") is not None:
            revision = self.sources.get_revision(str(latest["revision_id"]))
            if revision is not None:
                digest = str(revision["content_hash"])
                blob = {
                    "content_hash": digest,
                    "available": self.blobs.has(digest, verify=True),
                }
        return {
            "schema": "testamur.source-gateway.status.v1",
            "source": source,
            "latest_snapshot": latest,
            "latest_revision": revision,
            "blob": blob,
        }

    def revalidate(
        self,
        source_id: str,
        *,
        session_id: str | None = None,
        actor: Mapping[str, Any] | None = None,
        policy: SourceFetchPolicy | None = None,
    ) -> GatewayCapture:
        source = self.sources.get_source(str(source_id))
        if source is None:
            raise KeyError(source_id)
        return self.fetch(
            str(source["initial_locator"]),
            session_id=session_id,
            actor=actor,
            policy=policy,
        )

    def refresh_watch(
        self,
        watch_id: str,
        *,
        session_id: str | None = None,
        actor: Mapping[str, Any] | None = None,
        policy: SourceFetchPolicy | None = None,
    ) -> dict[str, Any]:
        watch = self.watches.get_watch(str(watch_id))
        if watch is None:
            raise KeyError(watch_id)
        capture = self.revalidate(
            str(watch["source_id"]),
            session_id=session_id,
            actor=actor,
            policy=policy,
        )
        evaluated = self.watches.evaluate_snapshot(
            self.sources,
            watch_id=str(watch_id),
            snapshot_id=str(capture.snapshot["snapshot_id"]),
        )
        return {
            "schema": "testamur.source-gateway.watch-refresh.v1",
            "capture": capture.metadata(),
            "evaluation": evaluated["evaluation"],
            "alert": evaluated.get("alert"),
            "reused_evaluation": bool(evaluated.get("reused")),
            "semantics": {
                "operational_observation": True,
                "truth_change_implied": False,
                "downstream_invalidity_implied": False,
            },
        }
