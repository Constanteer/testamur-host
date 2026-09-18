from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ObjectKind, classify_object_ref, error_envelope, object_envelope
from .product_extensions import ProductExtensions
from .project_store import TestamurProjectStore
from .record_store import TestamurRecordStore
from .source_store import TestamurSourceStore
from .watch_store import TestamurWatchStore


class TestamurProductService:
    """Coherent local read surface over canonical Testamur kernels.

    Existing stores remain the owners of durable semantics. W2-W5 capabilities
    enter only through explicit canonical extension hooks; absence is reported,
    never guessed or silently reconstructed from legacy Witness state.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        extensions: ProductExtensions | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sources = TestamurSourceStore(self.database_path)
        self.records = TestamurRecordStore(self.database_path)
        self.watches = TestamurWatchStore(self.database_path)
        self.projects = TestamurProjectStore(self.database_path)
        self.extensions = extensions or ProductExtensions.empty()

    @classmethod
    def integrated(cls, database_path: str | Path, **extension_options: Any) -> "TestamurProductService":
        """Build the product surface with the canonical integrated W3-W5 stores.

        Reliance, temporal and impact remain explicit options because they require
        semantic context owned by their engines. The constructor never invents a
        policy/evidence view or temporal provenance merely to make a capability
        appear available.
        """
        from .product_adapters import canonical_product_extensions

        extensions = canonical_product_extensions(database_path, **extension_options)
        return cls(database_path, extensions=extensions)

    def project(self, ref: str) -> dict[str, Any]:
        project = self.projects.get_project(ref)
        if project is None:
            return error_envelope(
                "object_not_found",
                "no project exists for this reference",
                details={"ref": ref},
            )
        monitors: list[dict[str, Any]] = []
        for link in self.projects.monitors_for_project(str(project["project_id"])):
            watch = self.watches.get_watch(link["watch_id"])
            if watch is None:
                continue
            source = self.sources.get_source(link["source_id"])
            try:
                revision = self.watches.latest_watch_revision(link["watch_id"])
                evaluation = self.watches.latest_evaluation(link["watch_id"])
            except KeyError:
                revision = None
                evaluation = None
            monitors.append(
                {
                    **link,
                    "label": None if revision is None else revision.get("label"),
                    "alert_on": [] if revision is None else list(revision.get("alert_on") or []),
                    "interval_seconds": None if revision is None else revision.get("interval_seconds"),
                    "latest_state": None if evaluation is None else evaluation.get("operational_state"),
                    "last_evaluated_at": None if evaluation is None else evaluation.get("recorded_at"),
                    "locator": None if source is None else source.get("initial_locator"),
                }
            )
        supply_chain = None
        for relation in self.records.relations_for(
            str(project["project_id"]),
            direction="outgoing",
            relation_types=["cites"],
            limit=100,
        ):
            revision = self.records.get_revision(str(relation.get("to_ref") or ""))
            if revision is None:
                continue
            record = self.records.get_record(str(revision.get("record_id") or ""))
            if record is None or record.get("record_kind") != "supply-chain-scan":
                continue
            try:
                statement = json.loads(str(revision.get("statement") or "{}"))
            except json.JSONDecodeError:
                continue
            manifest_revision_ids = [
                str(item)
                for item in statement.get("manifest_revision_ids") or []
                if isinstance(item, str) and item
            ]
            dependency_revision_ids = [
                str(item)
                for item in statement.get("dependency_record_revision_ids") or []
                if isinstance(item, str) and item
            ]

            manifests: list[dict[str, Any]] = []
            for manifest_revision_id in manifest_revision_ids[:200]:
                manifest_revision = self.records.get_revision(manifest_revision_id)
                if manifest_revision is None:
                    continue
                try:
                    manifest_statement = json.loads(
                        str(manifest_revision.get("statement") or "{}")
                    )
                except json.JSONDecodeError:
                    continue
                if (
                    manifest_statement.get("schema")
                    != "testamur.supply-chain.manifest.v1"
                ):
                    continue
                dependency_ids = manifest_statement.get("dependency_revision_ids")
                manifests.append(
                    {
                        "record_id": manifest_revision.get("record_id"),
                        "record_revision_id": manifest_revision_id,
                        "recorded_at": manifest_revision.get("recorded_at"),
                        "path": manifest_statement.get("path"),
                        "parser": manifest_statement.get("parser"),
                        "sha256": manifest_statement.get("sha256"),
                        "dependency_count": len(dependency_ids)
                        if isinstance(dependency_ids, list)
                        else 0,
                    }
                )

            dependencies: list[dict[str, Any]] = []
            for dependency_revision_id in dependency_revision_ids[:1000]:
                dependency_revision = self.records.get_revision(
                    dependency_revision_id
                )
                if dependency_revision is None:
                    continue
                try:
                    dependency_statement = json.loads(
                        str(dependency_revision.get("statement") or "{}")
                    )
                except json.JSONDecodeError:
                    continue
                if (
                    dependency_statement.get("schema")
                    != "testamur.supply-chain.dependency.v1"
                ):
                    continue
                component_revision = dependency_statement.get(
                    "component_revision"
                )
                if not isinstance(component_revision, dict):
                    component_revision = {}
                component = component_revision.get("component")
                if not isinstance(component, dict):
                    component = {}
                observed_in = dependency_statement.get("observed_in")
                dependencies.append(
                    {
                        "record_id": dependency_revision.get("record_id"),
                        "record_revision_id": dependency_revision_id,
                        "recorded_at": dependency_revision.get("recorded_at"),
                        "component_revision_id": component_revision.get(
                            "component_revision_id"
                        ),
                        "component_id": component.get("component_id"),
                        "ecosystem": component.get("namespace"),
                        "name": component.get("name"),
                        "version": component_revision.get("version"),
                        "digest": component_revision.get("digest"),
                        "locator": component_revision.get("locator"),
                        "identity_strength": component_revision.get(
                            "identity_strength"
                        ),
                        "is_exact_revision": bool(
                            component_revision.get("is_exact_revision")
                        ),
                        "direct": dependency_statement.get("direct"),
                        "observed_in": list(observed_in)
                        if isinstance(observed_in, list)
                        else [],
                    }
                )

            dependencies.sort(
                key=lambda item: (
                    str(item.get("ecosystem") or ""),
                    str(item.get("name") or ""),
                    str(item.get("version") or ""),
                )
            )
            manifests.sort(key=lambda item: str(item.get("path") or ""))
            supply_chain = {
                "scan_record_id": record["record_id"],
                "scan_revision_id": revision["revision_id"],
                "recorded_at": revision.get("recorded_at"),
                "manifest_count": len(manifest_revision_ids),
                "dependency_count": len(dependency_revision_ids),
                "manifests": manifests,
                "dependencies": dependencies,
                "inventory_truncated": (
                    len(manifest_revision_ids) > len(manifests)
                    or len(dependency_revision_ids) > len(dependencies)
                ),
                "warnings": list(statement.get("warnings") or []),
                "semantics": dict(statement.get("semantics") or {}),
            }
            break

        return {
            "ok": True,
            "schema": "testamur.product.project.v2",
            "project": project,
            "monitors": monitors,
            "supply_chain": supply_chain,
            "semantics": {
                "project_is_container": True,
                "monitor_count": len(monitors),
                "sources_are_monitor_targets": True,
                "supply_chain_is_manifest_observation": supply_chain is not None,
            },
        }

    def get_object(self, ref: str) -> dict[str, Any]:
        parsed = classify_object_ref(ref)
        getter = {
            ObjectKind.SOURCE: self.sources.get_source,
            ObjectKind.REVISION: self.sources.get_revision,
            ObjectKind.SNAPSHOT: self.sources.get_snapshot,
            ObjectKind.RECORD: self.records.get_record,
            ObjectKind.RECORD_REVISION: self.records.get_revision,
            ObjectKind.RELATION: self.records.get_relation,
            ObjectKind.WATCH: self.watches.get_watch,
            ObjectKind.WATCH_REVISION: self.watches.get_watch_revision,
            ObjectKind.WATCH_EVALUATION: self.watches.get_evaluation,
            ObjectKind.ALERT: self.watches.get_alert,
        }.get(parsed.kind)
        if getter is None:
            getter = self.extensions.reader_for(parsed.kind)
        if getter is None:
            return error_envelope(
                "unsupported_object_kind",
                f"product read surface has no registered reader for {parsed.kind.value}",
                details={"ref": ref, "kind": parsed.kind.value},
            )
        payload = getter(ref)
        if payload is None:
            return error_envelope(
                "object_not_found",
                "no canonical Testamur object exists for this reference",
                details={"ref": ref, "kind": parsed.kind.value},
            )
        return object_envelope(parsed, payload, schema="testamur.product.object.v1")

    def history(self, ref: str, *, limit: int = 50) -> dict[str, Any]:
        parsed = classify_object_ref(ref)
        try:
            if parsed.kind is ObjectKind.SOURCE:
                values = self.sources.history(ref, limit=limit)
                item_kind = ObjectKind.SNAPSHOT.value
            elif parsed.kind is ObjectKind.RECORD:
                values = self.records.history(ref, limit=limit)
                item_kind = ObjectKind.RECORD_REVISION.value
            elif parsed.kind is ObjectKind.WATCH:
                current = self.watches.latest_watch_revision(ref)
                values = [] if current is None else [current]
                item_kind = ObjectKind.WATCH_REVISION.value
            else:
                return error_envelope(
                    "history_not_supported",
                    "history is defined only for persistent versioned product objects",
                    details={"ref": ref, "kind": parsed.kind.value},
                )
        except KeyError:
            return error_envelope(
                "object_not_found", "object does not exist", details={"ref": ref}
            )
        return {
            "ok": True,
            "schema": "testamur.product.history.v1",
            "object": parsed.to_json(),
            "item_kind": item_kind,
            "items": values,
            "semantics": {
                "order": "newest_first",
                "history_is_recorded_history": True,
                "history_is_not_generic_as_of": True,
            },
        }

    def compare(self, left_ref: str, right_ref: str) -> dict[str, Any]:
        left = classify_object_ref(left_ref)
        right = classify_object_ref(right_ref)
        if left.kind is not right.kind:
            return error_envelope(
                "incompatible_compare",
                "mechanical comparison requires references of the same revision family",
                details={"left_kind": left.kind.value, "right_kind": right.kind.value},
            )
        try:
            if left.kind is ObjectKind.SNAPSHOT:
                comparison = self.sources.compare_snapshots(left_ref, right_ref)
            elif left.kind is ObjectKind.RECORD_REVISION:
                comparison = self.records.compare_revisions(left_ref, right_ref)
            else:
                return error_envelope(
                    "compare_not_supported",
                    "this object family has no canonical mechanical comparison",
                    details={"kind": left.kind.value},
                )
        except KeyError as exc:
            return error_envelope(
                "object_not_found",
                "one comparison object does not exist",
                details={"ref": str(exc.args[0])},
            )
        except ValueError as exc:
            return error_envelope(
                "incompatible_compare", str(exc), details={"left": left_ref, "right": right_ref}
            )
        return {
            "ok": True,
            "schema": "testamur.product.compare.v1",
            "left": left.to_json(),
            "right": right.to_json(),
            "comparison": comparison,
            "semantics": {
                "mechanical_only": True,
                "semantic_equivalence_inferred": False,
                "validity_or_truth_change_inferred": False,
            },
        }

    def impact(self, ref: str) -> dict[str, Any]:
        if self.extensions.impact_reader is None:
            return error_envelope(
                "impact_capability_unavailable",
                "no canonical reliance/lineage impact provider is registered",
                details={"ref": ref},
            )
        return dict(self.extensions.impact_reader(ref))

    def temporal(self, ref: str, query: dict[str, Any]) -> dict[str, Any]:
        if self.extensions.temporal_reader is None:
            return error_envelope(
                "temporal_capability_unavailable",
                "no canonical temporal provider is registered",
                details={"ref": ref},
            )
        try:
            return dict(self.extensions.temporal_reader(ref, query))
        except ValueError as exc:
            return error_envelope(
                "invalid_temporal_query", str(exc), details={"ref": ref}
            )

    def temporal_events(self, ref: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.extensions.temporal_event_reader is None:
            return error_envelope(
                "temporal_event_capability_unavailable",
                "no canonical temporal event provider is registered",
                details={"ref": ref},
            )
        try:
            return dict(self.extensions.temporal_event_reader(ref, query or {}))
        except ValueError as exc:
            return error_envelope(
                "invalid_temporal_event_query", str(exc), details={"ref": ref}
            )

    def dashboard(self, *, limit: int = 30) -> dict[str, Any]:
        """Project the canonical stores into a product-home activity surface.

        This is intentionally a read projection. It does not create new durable
        object kinds or infer trust, truth, ownership, or invalidity.
        """
        bounded = max(1, min(int(limit), 100))

        with self.sources.connect() as conn:
            source_rows = conn.execute(
                "SELECT record_json, created_at FROM testamur_sources "
                "ORDER BY created_at DESC, source_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            snapshot_rows = conn.execute(
                "SELECT record_json FROM testamur_source_snapshots "
                "ORDER BY recorded_at DESC, snapshot_id DESC LIMIT ?",
                (bounded * 2,),
            ).fetchall()

        with self.records.connect() as conn:
            record_rows = conn.execute(
                "SELECT record_json FROM testamur_records "
                "ORDER BY created_at DESC, record_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT record_json FROM testamur_record_revisions "
                "ORDER BY recorded_at DESC, revision_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()

        with self.watches.connect() as conn:
            watch_rows = conn.execute(
                "SELECT record_json FROM testamur_watches "
                "ORDER BY created_at DESC, watch_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            alert_rows = conn.execute(
                "SELECT record_json FROM testamur_alerts "
                "ORDER BY recorded_at DESC, alert_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()

        snapshots = [json.loads(str(row["record_json"])) for row in snapshot_rows]
        recent_records = [json.loads(str(row["record_json"])) for row in record_rows]
        revisions = [json.loads(str(row["record_json"])) for row in revision_rows]
        alerts = [json.loads(str(row["record_json"])) for row in alert_rows]

        watches: list[dict[str, Any]] = []
        watches_by_id: dict[str, dict[str, Any]] = {}
        for row in watch_rows:
            watch = json.loads(str(row["record_json"]))
            watch_id = str(watch.get("watch_id") or "")
            try:
                revision = self.watches.latest_watch_revision(watch_id)
                evaluation = self.watches.latest_evaluation(watch_id)
            except KeyError:
                revision = None
                evaluation = None
            enriched = {
                **watch,
                "project_id": self.projects.project_id_for_watch(watch_id),
                "label": None if revision is None else revision.get("label"),
                "alert_on": [] if revision is None else list(revision.get("alert_on") or []),
                "interval_seconds": None if revision is None else revision.get("interval_seconds"),
                "latest_state": None if evaluation is None else evaluation.get("operational_state"),
                "last_evaluated_at": None if evaluation is None else evaluation.get("recorded_at"),
            }
            watches.append(enriched)
            watches_by_id[watch_id] = enriched

        projects: list[dict[str, Any]] = []
        for project in self.projects.list_projects()[:bounded]:
            links = self.projects.monitors_for_project(str(project["project_id"]))
            monitor_values = [
                watches_by_id[link["watch_id"]]
                for link in links
                if link["watch_id"] in watches_by_id
            ]
            activity_times = [
                str(item.get("last_evaluated_at") or item.get("created_at") or "")
                for item in monitor_values
                if item.get("last_evaluated_at") or item.get("created_at")
            ]
            states = [str(item.get("latest_state") or "") for item in monitor_values]
            status = "empty"
            if any(state in {"changed", "unavailable"} for state in states):
                status = "attention"
            elif monitor_values:
                status = "active"
            projects.append(
                {
                    **project,
                    "ref": project["project_id"],
                    "watch_count": len(links),
                    "monitor_count": len(links),
                    "status": status,
                    "last_activity_at": max(activity_times) if activity_times else project.get("updated_at"),
                }
            )

        source_names: dict[str, str] = {}
        for row in source_rows:
            source = json.loads(str(row["record_json"]))
            source_names[str(source["source_id"])] = str(source.get("initial_locator") or source["source_id"])
        activity: list[dict[str, Any]] = []
        for snapshot in snapshots:
            source_id = str(snapshot.get("source_id") or "")
            activity.append(
                {
                    "kind": "observation",
                    "ref": snapshot.get("snapshot_id"),
                    "subject_ref": source_id,
                    "title": source_names.get(source_id, source_id),
                    "at": snapshot.get("recorded_at"),
                    "state": snapshot.get("status"),
                    "changed_revision": snapshot.get("revision_id"),
                }
            )
        for alert in alerts:
            source_id = str(alert.get("source_id") or "")
            activity.append(
                {
                    "kind": "alert",
                    "ref": alert.get("alert_id"),
                    "subject_ref": source_id,
                    "title": source_names.get(source_id, source_id),
                    "at": alert.get("recorded_at"),
                    "state": alert.get("event_type"),
                    "watch_id": alert.get("watch_id"),
                }
            )
        revision_by_record: dict[str, dict[str, Any]] = {}
        for revision in revisions:
            record_id = str(revision.get("record_id") or "")
            revision_by_record.setdefault(record_id, revision)
            activity.append(
                {
                    "kind": "record_revision",
                    "ref": revision.get("revision_id"),
                    "subject_ref": record_id,
                    "title": revision.get("title") or revision.get("statement") or record_id,
                    "at": revision.get("recorded_at"),
                    "state": f"revision {revision.get('ordinal', '—')}",
                }
            )
        activity.sort(key=lambda item: str(item.get("at") or ""), reverse=True)

        records = [
            {
                **record,
                "latest_revision": revision_by_record.get(str(record.get("record_id") or "")),
            }
            for record in recent_records
        ]

        return {
            "ok": True,
            "schema": "testamur.product.dashboard.v1",
            "projects": projects,
            "feed": activity[:bounded],
            "watches": watches,
            "alerts": alerts,
            "records": records,
            "status": self.status(),
            "semantics": {
                "read_projection_only": True,
                "projects_are_containers": True,
                "project_entries_are_tracked_sources": False,
                "monitors_link_projects_to_sources": True,
                "feed_is_recorded_activity": True,
                "truth_or_validity_not_inferred": True,
            },
        }

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "testamur.product.status.v1",
            "sources": self.sources.stats(),
            "records": self.records.stats(),
            "watches": self.watches.stats(),
            "projects": self.projects.stats(),
            "extensions": self.extensions.capabilities(),
        }
