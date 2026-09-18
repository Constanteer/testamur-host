from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from .contracts import error_envelope
from .product_service import TestamurProductService
from .source_gateway import TestamurSourceGateway


def create_project(
    service: TestamurProductService,
    *,
    name: str,
    description: str | None = None,
    visibility: str = "private",
) -> dict[str, Any]:
    """Create a durable product Project container.

    Projects group zero or more canonical Watches. They are not Sources and do
    not imply that anything is being monitored until a child monitor is added.
    """

    try:
        project = service.projects.create_project(
            name=name,
            description=description,
            visibility=visibility,
        )
    except ValueError as exc:
        return error_envelope("invalid_project", str(exc))

    return {
        "ok": True,
        "schema": "testamur.product.project-write.v2",
        "created": True,
        "project_ref": project["project_id"],
        "project": project,
        "semantics": {
            "project_is_container": True,
            "project_is_source_presentation": False,
            "project_can_have_many_monitors": True,
            "truth_or_validity_inferred": False,
        },
    }


def create_monitor(
    service: TestamurProductService,
    *,
    project_id: str | None = None,
    locator: str | None = None,
    source_id: str | None = None,
    provider: str = "source",
    provider_config: dict[str, Any] | None = None,
    label: str | None = None,
    interval_seconds: int | None = None,
    alert_on: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Create a canonical Watch and optionally attach it to a Project container."""

    project_ref = None if project_id is None else str(project_id).strip()
    if project_ref and service.projects.get_project(project_ref) is None:
        return error_envelope(
            "object_not_found",
            "no project exists for this monitor",
            details={"project_id": project_ref},
        )

    source_ref = None if source_id is None else str(source_id).strip()
    locator_value = None if locator is None else str(locator).strip()
    provider_name = str(provider or "source").strip() or "source"

    if provider_name == "source":
        if source_ref and locator_value:
            return error_envelope("invalid_monitor", "provide locator or source_id, not both")
        if not source_ref and not locator_value:
            return error_envelope("invalid_monitor", "source monitor requires locator or source_id")
    else:
        if source_ref or locator_value:
            return error_envelope(
                "invalid_monitor",
                "plugin monitor providers use provider_config rather than locator/source_id",
            )
        resolver = service.extensions.monitor_target_provider(provider_name)
        if resolver is None:
            return error_envelope(
                "monitor_provider_unavailable",
                f"no monitor target provider is registered for {provider_name}",
                details={"provider": provider_name},
            )
        try:
            resolved = dict(resolver(dict(provider_config or {})))
        except (KeyError, TypeError, ValueError) as exc:
            return error_envelope(
                "invalid_monitor_provider_config",
                str(exc),
                details={"provider": provider_name},
            )
        source_ref = str(resolved.get("source_id") or "").strip() or None
        locator_value = str(resolved.get("locator") or "").strip() or None
        if source_ref and locator_value:
            return error_envelope(
                "invalid_monitor_provider_result",
                "monitor provider must resolve source_id or locator, not both",
                details={"provider": provider_name},
            )
        if not source_ref and not locator_value:
            return error_envelope(
                "invalid_monitor_provider_result",
                "monitor provider did not resolve source_id or locator",
                details={"provider": provider_name},
            )

    if locator_value:
        try:
            source = service.sources.get_or_create_source(locator_value)
        except ValueError as exc:
            return error_envelope("invalid_monitor", str(exc), details={"locator": locator_value})
        source_ref = str(source["source_id"])

    assert source_ref is not None
    normalized_label = None if label is None or not str(label).strip() else str(label).strip()
    selected = None if alert_on is None else [str(value).strip() for value in alert_on]

    try:
        created = service.watches.create_watch(
            service.sources,
            source_id=source_ref,
            label=normalized_label,
            alert_on=selected,
            interval_seconds=interval_seconds,
        )
        watch_id = str(created["watch"]["watch_id"])
        if project_ref:
            service.projects.link_monitor(
                project_id=project_ref,
                watch_id=watch_id,
                source_id=source_ref,
            )
        latest = service.sources.latest_recorded_snapshot(source_ref)
        evaluation = None
        initial_capture = None
        baseline_error = None
        if latest is not None:
            evaluation = service.watches.evaluate_snapshot(
                service.sources,
                watch_id=watch_id,
                snapshot_id=latest["snapshot_id"],
            )
        else:
            # Establish an operational baseline immediately so a newly-created
            # monitor is useful without a second, hidden action. Operational
            # retrieval failures become durable observations; policy/locator
            # rejection does not roll back the valid monitor object.
            try:
                with TestamurSourceGateway(service.database_path) as gateway:
                    refreshed = gateway.refresh_watch(
                        watch_id,
                        actor={"ref": "testamur:product-monitor-create"},
                    )
                initial_capture = refreshed["capture"]
                evaluation = {
                    "evaluation": refreshed["evaluation"],
                    "alert": refreshed.get("alert"),
                    "reused": bool(refreshed.get("reused_evaluation")),
                }
            except (KeyError, ValueError) as exc:
                baseline_error = str(exc)
    except KeyError:
        return error_envelope(
            "object_not_found",
            "no tracked source exists for this monitor",
            details={"source_id": source_ref},
        )
    except ValueError as exc:
        return error_envelope("invalid_monitor", str(exc), details={"source_id": source_ref})

    return {
        "ok": True,
        "schema": "testamur.product.monitor-write.v2",
        "project_id": project_ref,
        "provider": provider_name,
        "source_id": source_ref,
        "interval_seconds": created["revision"].get("interval_seconds"),
        "watch": created["watch"],
        "revision": created["revision"],
        "initial_evaluation": evaluation,
        "initial_capture": initial_capture,
        "baseline_error": baseline_error,
        "semantics": {
            "canonical_watch_created": True,
            "project_membership_recorded": project_ref is not None,
            "project_identity_modified": False,
            "truth_status_implied": False,
        },
    }


def refresh_monitor(
    service: TestamurProductService,
    *,
    watch_id: str,
) -> dict[str, Any]:
    """Revalidate one monitor target and mechanically evaluate its Watch."""

    ref = str(watch_id).strip()
    if not ref:
        return error_envelope("invalid_monitor", "watch_id must not be empty")
    if service.watches.get_watch(ref) is None:
        return error_envelope(
            "object_not_found",
            "no monitor exists for this watch id",
            details={"watch_id": ref},
        )

    try:
        with TestamurSourceGateway(service.database_path) as gateway:
            refreshed = gateway.refresh_watch(
                ref,
                actor={"ref": "testamur:product-monitor-refresh"},
            )
    except KeyError:
        return error_envelope(
            "object_not_found",
            "monitor or source no longer exists",
            details={"watch_id": ref},
        )
    except ValueError as exc:
        return error_envelope(
            "invalid_monitor_refresh",
            str(exc),
            details={"watch_id": ref},
        )

    return {
        "ok": True,
        "schema": "testamur.product.monitor-refresh.v1",
        "watch_id": ref,
        "project": service.projects.project_for_watch(ref),
        "capture": refreshed["capture"],
        "evaluation": refreshed["evaluation"],
        "alert": refreshed.get("alert"),
        "reused_evaluation": bool(refreshed.get("reused_evaluation")),
        "semantics": {
            "operational_observation": True,
            "truth_change_implied": False,
            "downstream_invalidity_implied": False,
        },
    }


def refresh_project_monitors(
    service: TestamurProductService,
    *,
    project_ref: str,
) -> dict[str, Any]:
    """Refresh every monitor currently attached to one Project container."""

    ref = str(project_ref).strip()
    project = service.projects.get_project(ref)
    if project is None:
        return error_envelope(
            "object_not_found",
            "no project exists for this reference",
            details={"project_ref": ref},
        )

    links = service.projects.monitors_for_project(str(project["project_id"]))
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    alerts = 0

    for link in links:
        watch_id = str(link["watch_id"])
        refreshed = refresh_monitor(service, watch_id=watch_id)
        ok = refreshed.get("ok") is True
        if ok:
            succeeded += 1
            if refreshed.get("alert") is not None:
                alerts += 1
        else:
            failed += 1
        results.append(
            {
                "watch_id": watch_id,
                "source_id": link["source_id"],
                "ok": ok,
                "evaluation": refreshed.get("evaluation") if ok else None,
                "alert": refreshed.get("alert") if ok else None,
                "error": None if ok else refreshed.get("error"),
            }
        )

    return {
        "ok": True,
        "schema": "testamur.product.project-refresh.v1",
        "project": project,
        "results": results,
        "summary": {
            "total": len(links),
            "succeeded": succeeded,
            "failed": failed,
            "alerts": alerts,
        },
        "semantics": {
            "best_effort_per_monitor": True,
            "one_monitor_failure_does_not_abort_project_refresh": True,
            "operational_change_implies_truth_change": False,
            "operational_change_implies_downstream_invalidity": False,
        },
    }


def _parse_recorded_time(value: str) -> datetime:
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_due_monitors(
    service: TestamurProductService,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh monitors whose explicit cadence is due.

    Manual monitors (interval_seconds is absent/None) are intentionally skipped.
    """

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due: list[tuple[str, int]] = []
    skipped_manual = 0
    skipped_not_due = 0

    with service.watches.connect() as connection:
        rows = connection.execute(
            "SELECT watch_id FROM testamur_watches ORDER BY created_at, watch_id"
        ).fetchall()

    for row in rows:
        watch_id = str(row["watch_id"])
        revision = service.watches.latest_watch_revision(watch_id)
        interval_raw = None if revision is None else revision.get("interval_seconds")
        if interval_raw is None:
            skipped_manual += 1
            continue
        interval = int(interval_raw)
        evaluation = service.watches.latest_evaluation(watch_id)
        if evaluation is None:
            due.append((watch_id, interval))
            continue
        last = _parse_recorded_time(str(evaluation["recorded_at"]))
        if (current - last).total_seconds() >= interval:
            due.append((watch_id, interval))
        else:
            skipped_not_due += 1

    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    alerts = 0
    for watch_id, interval in due:
        refreshed = refresh_monitor(service, watch_id=watch_id)
        ok = refreshed.get("ok") is True
        if ok:
            succeeded += 1
            if refreshed.get("alert") is not None:
                alerts += 1
        else:
            failed += 1
        results.append(
            {
                "watch_id": watch_id,
                "interval_seconds": interval,
                "ok": ok,
                "evaluation": refreshed.get("evaluation") if ok else None,
                "alert": refreshed.get("alert") if ok else None,
                "error": None if ok else refreshed.get("error"),
            }
        )

    return {
        "ok": True,
        "schema": "testamur.product.monitor-due-run.v1",
        "results": results,
        "summary": {
            "due": len(due),
            "succeeded": succeeded,
            "failed": failed,
            "alerts": alerts,
            "skipped_manual": skipped_manual,
            "skipped_not_due": skipped_not_due,
        },
        "semantics": {
            "automatic_refresh_requires_explicit_cadence": True,
            "manual_monitors_never_run_due": True,
            "best_effort_per_monitor": True,
        },
    }
