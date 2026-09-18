from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .blob_retention import TestamurBlobRetentionStore
from .environment import discover
from .user_file_ingest import DEFAULT_MAX_UPLOAD_BYTES, TestamurUserFileIngest


def _error(code: str, message: str, *, machine: bool) -> int:
    if machine:
        print(json.dumps({"ok": False, "schema": "testamur.error.v1", "error": {"code": code, "message": message}}, sort_keys=True))
    else:
        print(f"testamur source: {message}", file=sys.stderr)
    return 1


def dispatch(argv: Sequence[str], *, machine: bool = False) -> int:
    parser = argparse.ArgumentParser(prog="testamur source", description="Manage Testamur Sources")
    sub = parser.add_subparsers(dest="command", required=True)

    upload = sub.add_parser("upload", help="ingest a local file as a private Source")
    upload.add_argument("path")
    upload.add_argument("--media-type")
    upload.add_argument(
        "--owner-subject",
        default=os.environ.get("TESTAMUR_OWNER_SUBJECT", "local:user"),
        help="trusted local owner subject (default: local:user)",
    )

    status = sub.add_parser("retention-status", help="show raw-byte retention state")
    status.add_argument("source_id")
    status.add_argument(
        "--owner-subject",
        default=os.environ.get("TESTAMUR_OWNER_SUBJECT", "local:user"),
        help="trusted local owner subject (default: local:user)",
    )

    retention = sub.add_parser("retention", help="set raw-byte retention policy")
    retention.add_argument("source_id")
    retention.add_argument("policy", choices=("retain", "purge_on_request"))
    retention.add_argument(
        "--owner-subject",
        default=os.environ.get("TESTAMUR_OWNER_SUBJECT", "local:user"),
        help="trusted local owner subject (default: local:user)",
    )

    purge = sub.add_parser("purge", help="release and purge eligible raw bytes")
    purge.add_argument("source_id")
    purge.add_argument(
        "--owner-subject",
        default=os.environ.get("TESTAMUR_OWNER_SUBJECT", "local:user"),
        help="trusted local owner subject (default: local:user)",
    )

    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    env = discover()
    if env is None:
        return _error(
            "environment_required",
            "not inside a Testamur environment; run testamur init first",
            machine=machine,
        )

    if args.command == "upload":
        path = Path(args.path).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return _error("file_not_found", "source upload file does not exist", machine=machine)
        if not resolved.is_file():
            return _error("not_a_file", f"not a regular file: {path}", machine=machine)
        try:
            size = resolved.stat().st_size
        except OSError:
            return _error("file_stat_failed", "could not inspect source upload file", machine=machine)
        if size > DEFAULT_MAX_UPLOAD_BYTES:
            return _error(
                "upload_too_large",
                f"upload is {size} bytes, above max_upload_bytes={DEFAULT_MAX_UPLOAD_BYTES}",
                machine=machine,
            )
        try:
            content = resolved.read_bytes()
            ingest = TestamurUserFileIngest(
                env.database_path,
                env.root / ".testamur" / "blobs",
            )
            value = ingest.ingest_bytes(
                content,
                filename=resolved.name,
                owner_subject=str(args.owner_subject),
                media_type=args.media_type,
            )
        except OSError:
            return _error("file_read_failed", "could not read source upload file", machine=machine)
        except ValueError as exc:
            return _error("invalid_upload", str(exc), machine=machine)

        payload = {
            "ok": True,
            "schema": "testamur.source-upload.v1",
            "source": value["source"],
            "snapshot": value["snapshot"],
            "policy": value["policy"],
            "private_metadata": value["private_metadata"],
            "blob": value["blob"],
        }
        if machine:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"Uploaded {value['private_metadata']['display_name']}")
            print(f"source      {value['source']['source_id']}")
            print(f"revision    {value['snapshot']['revision_id']}")
            print("visibility  private")
            print("external processing  disabled")
        return 0


    if args.command in {"retention-status", "retention", "purge"}:
        service = TestamurBlobRetentionStore(
            env.database_path,
            env.root / ".testamur" / "blobs",
        )
        source_id = str(args.source_id)
        source = service.sources.get_source(source_id)
        if source is None:
            return _error("source_not_found", "source does not exist", machine=machine)
        policy = service.access.latest_policy(source_id)
        owner = str(args.owner_subject)
        if policy is None:
            return _error(
                "retention_policy_missing",
                "source has no access policy; retention fails closed",
                machine=machine,
            )
        if policy.get("owner_subject") != owner:
            return _error(
                "retention_not_authorized",
                "owner subject is not authorized for source retention",
                machine=machine,
            )

        if args.command == "retention-status":
            digests = []
            for digest in service.source_digests(source_id):
                state = service.retention_status(digest)
                event = service.latest_event(source_id, digest)
                release_current = bool(
                    event
                    and event.get("action") == "release"
                    and event.get("policy_revision_id") == policy.get("policy_revision_id")
                    and policy.get("raw_bytes_policy") == "purge_on_request"
                )
                digests.append(
                    {
                        **state,
                        "source_reference_active": not release_current,
                        "latest_event": event,
                    }
                )
            payload = {
                "ok": True,
                "schema": "testamur.source-retention.v1",
                "source_id": source_id,
                "policy": policy,
                "digests": digests,
                "semantics": {
                    "raw_bytes_separate_from_evidence_identity": True,
                    "status_is_projection_not_reservation": True,
                },
            }
        elif args.command == "retention":
            new_policy = service.access.record_policy(
                source_id,
                owner_subject=owner,
                visibility=str(policy["visibility"]),
                raw_bytes_policy=str(args.policy),
                allow_external_processing=bool(policy["allow_external_processing"]),
            )
            retained = (
                service.retain_source(source_id, owner_subject=owner)
                if args.policy == "retain"
                else None
            )
            payload = {
                "ok": True,
                "schema": "testamur.source-retention-write.v1",
                "source_id": source_id,
                "policy": new_policy,
                "retention": retained,
                "semantics": {
                    "raw_bytes_deleted": False,
                    "retain_restores_missing_bytes": False,
                },
            }
        else:
            try:
                result = service.purge_source(source_id, owner_subject=owner)
            except PermissionError as exc:
                return _error("purge_not_permitted", str(exc), machine=machine)
            payload = {
                "ok": True,
                "schema": "testamur.source-purge.v1",
                **result,
            }

        if machine:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            if args.command == "retention-status":
                print(f"source      {source_id}")
                print(f"policy      {policy['raw_bytes_policy']}")
                print(f"digests     {len(payload['digests'])}")
            elif args.command == "retention":
                print(f"source      {source_id}")
                print(f"policy      {payload['policy']['raw_bytes_policy']}")
                print("raw bytes   unchanged")
            else:
                print(f"source      {source_id}")
                for item in payload["digests"]:
                    print(f"{item['content_hash']}  {item['outcome']}")
        return 0

    return _error("unknown_command", "unknown source command", machine=machine)


__all__ = ["dispatch"]
