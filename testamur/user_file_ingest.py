from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any

from .blob_retention import TestamurBlobRetentionStore
from .blob_store import TestamurBlobStore, sha256_digest
from .private_source_metadata import TestamurPrivateSourceMetadataStore
from .source_access import TestamurSourceAccessStore
from .source_store import TestamurSourceStore


DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _safe_client_filename(value: str) -> str:
    raw = str(value).replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        return "upload.bin"
    cleaned = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    return cleaned[:255] or "upload.bin"


class TestamurUserFileIngest:
    """Ingest exact user-provided bytes as a private Source by default.

    The immutable Source/Snapshot/Revision layer never stores the caller's local
    path or convenience filename/media type. Those values live only in the
    erasable owner-private metadata store.

    Missing access policy is fail-closed, so partial failures cannot make a
    newly-created Source publicly readable.
    """

    def __init__(
        self,
        database_path: str | Path,
        blob_root: str | Path,
        *,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.database_path = Path(database_path)
        self.sources = TestamurSourceStore(self.database_path)
        self.access = TestamurSourceAccessStore(self.database_path)
        self.private_metadata = TestamurPrivateSourceMetadataStore(self.database_path)
        self.blobs = TestamurBlobStore(blob_root)
        self.retention = TestamurBlobRetentionStore(self.database_path, blob_root)
        self.max_upload_bytes = int(max_upload_bytes)
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")

    def ingest_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        owner_subject: str,
        media_type: str | None = None,
        visibility: str = "private",
        allow_external_processing: bool = False,
        raw_bytes_policy: str = "retain",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        value = bytes(content)
        if len(value) > self.max_upload_bytes:
            raise ValueError(
                f"upload is {len(value)} bytes, above max_upload_bytes={self.max_upload_bytes}"
            )
        if str(visibility).strip().lower() != "private":
            raise ValueError(
                "initial user-file upload visibility must be private; "
                "publish with a later access-policy revision"
            )
        if not isinstance(allow_external_processing, bool):
            raise ValueError("allow_external_processing must be a boolean")

        safe_name = _safe_client_filename(filename)
        resolved_media_type = (
            str(media_type).strip()
            if media_type is not None and str(media_type).strip()
            else mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        )

        opaque_locator = f"upload:{uuid.uuid4().hex}"
        source = self.sources.get_or_create_source(opaque_locator)
        source_id = str(source["source_id"])

        # Claim the digest before CAS materialization. Purge serializes its final
        # blocker check with new claims, closing the put -> Snapshot persistence
        # race without treating the operational claim as durable evidence.
        digest = sha256_digest(value)
        claim = self.retention.claim(source_id, digest, purpose="user_upload")
        try:
            blob = self.blobs.put(value)

            # Access policy is separate from evidence identity. Absence is
            # fail-closed, and the initial policy is always private.
            policy = self.access.record_policy(
                source_id,
                owner_subject=owner_subject,
                visibility="private",
                raw_bytes_policy=raw_bytes_policy,
                allow_external_processing=allow_external_processing,
            )
            private_metadata = self.private_metadata.set(
                source_id,
                display_name=safe_name,
                media_type=resolved_media_type,
            )

            snapshot = self.sources.record_snapshot(
                source_id,
                locator=opaque_locator,
                content=value,
                observed_at=observed_at,
                status="captured",
                retrieval_metadata={
                    "kind": "user_upload",
                    "byte_size": len(value),
                    "client_filename_persisted_in_snapshot": False,
                    "media_type_persisted_in_snapshot": False,
                    "original_local_path_persisted": False,
                    "access_policy_separate_from_snapshot_identity": True,
                    "publication_rights_implied": False,
                },
            )
            if snapshot.get("content_hash") != blob.get("content_hash"):
                raise RuntimeError("uploaded Snapshot digest does not match retained blob")
        finally:
            self.retention.release_claim(claim["claim_id"])

        return {
            "source": source,
            "snapshot": snapshot,
            "policy": policy,
            "private_metadata": private_metadata,
            "blob": blob,
        }


__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "TestamurUserFileIngest",
]
