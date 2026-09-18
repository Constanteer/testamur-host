#!/usr/bin/env python3
"""Fail-closed public demo entrypoint for MathHub/Witness.

The canonical mathhub.py server is a trusted workstation service with mutation
and Lean-execution endpoints. This entrypoint intentionally exposes the same
read/UI surface while rejecting every HTTP POST so a public demo can be placed
behind a reverse proxy or Cloudflare Tunnel without publishing unauthenticated
write/compute capabilities.
"""
from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

import mathhub
import server as core

HOST = os.environ.get("MATHHUB_PUBLIC_HOST", "127.0.0.1")
PORT = int(os.environ.get("MATHHUB_PUBLIC_PORT", "8000"))


class PublicDemoHandler(mathhub.Handler):
    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/health":
            runtime = core.runtime_status()
            self.json_response(
                200,
                {
                    "ok": True,
                    "mode": "public-read-only",
                    "runtime_ready": bool(runtime.get("ready")),
                    "environment_hash": runtime.get("environment_hash"),
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:
        self.json_response(
            403,
            {
                "ok": False,
                "error": "public demo is read-only",
                "mode": "public-read-only",
            },
        )


if __name__ == "__main__":
    print(f"MathHub public read-only demo: http://{HOST}:{PORT}")
    print("All POST endpoints are disabled; GET/UI routes remain available.")
    ThreadingHTTPServer((HOST, PORT), PublicDemoHandler).serve_forever()
