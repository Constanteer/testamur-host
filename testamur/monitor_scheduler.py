from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from .product_actions import run_due_monitors
from .product_service import TestamurProductService


class MonitorScheduler:
    """Small polling runner for explicit monitor cadences.

    The scheduler owns no cadence semantics. It repeatedly asks the canonical
    product action to run due monitors for already-open ProductService instances.
    Manual monitors remain excluded by run_due_monitors().
    """

    def __init__(
        self,
        services: Callable[[], Iterable[TestamurProductService]],
        *,
        poll_seconds: float = 60.0,
    ) -> None:
        seconds = float(poll_seconds)
        if seconds < 1:
            raise ValueError("monitor scheduler poll_seconds must be at least 1")
        self._services = services
        self.poll_seconds = seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_result: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="testamur-monitor-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.run_once()

    def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        services: list[TestamurProductService] = []
        seen: set[str] = set()
        for service in self._services():
            key = str(service.database_path.resolve())
            if key in seen:
                continue
            seen.add(key)
            services.append(service)

        workspaces: list[dict[str, Any]] = []
        total_due = 0
        total_succeeded = 0
        total_failed = 0
        total_alerts = 0
        runner_errors = 0

        for service in services:
            try:
                result = run_due_monitors(service)
            except Exception as exc:
                runner_errors += 1
                workspaces.append(
                    {
                        "database": str(service.database_path),
                        "ok": False,
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    }
                )
                continue

            summary = dict(result.get("summary") or {})
            total_due += int(summary.get("due") or 0)
            total_succeeded += int(summary.get("succeeded") or 0)
            total_failed += int(summary.get("failed") or 0)
            total_alerts += int(summary.get("alerts") or 0)
            workspaces.append(
                {
                    "database": str(service.database_path),
                    "ok": True,
                    "summary": summary,
                }
            )

        payload = {
            "ok": runner_errors == 0,
            "schema": "testamur.monitor-scheduler.run.v1",
            "started_at": started_at,
            "workspace_count": len(services),
            "workspaces": workspaces,
            "summary": {
                "due": total_due,
                "succeeded": total_succeeded,
                "failed": total_failed,
                "alerts": total_alerts,
                "runner_errors": runner_errors,
            },
            "semantics": {
                "poller_owns_no_cadence_semantics": True,
                "manual_monitors_remain_excluded": True,
                "only_loaded_workspaces_are_scanned": True,
            },
        }
        with self._lock:
            self.last_result = payload
        return payload
