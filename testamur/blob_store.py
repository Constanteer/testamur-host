from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any


class BlobIntegrityError(RuntimeError):
    """Stored bytes do not match the content-addressed identity."""


def normalize_sha256_digest(value: str) -> str:
    raw = str(value).strip()
    prefix = "sha256:"
    if not raw.startswith(prefix):
        raise ValueError("digest must use sha256:<64 hex>")
    hex_value = raw[len(prefix) :]
    if len(hex_value) != 64:
        raise ValueError("SHA-256 digest must contain exactly 64 hex characters")
    try:
        int(hex_value, 16)
    except ValueError as exc:
        raise ValueError("SHA-256 digest contains non-hexadecimal characters") from exc
    return prefix + hex_value.lower()


def sha256_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class TestamurBlobStore:
    """Opt-in local content-addressed byte storage.

    The blob store answers only byte identity/storage questions. Possessing bytes
    does not imply a right to publish or redistribute them. Source visibility,
    rights, publication class and access policy belong to higher layers.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        normalized = normalize_sha256_digest(digest)
        hex_value = normalized.split(":", 1)[1]
        return self.root / "sha256" / hex_value[:2] / hex_value[2:]

    def put(self, content: bytes) -> dict[str, Any]:
        value = bytes(content)
        digest = sha256_digest(value)
        target = self._path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            existing = target.read_bytes()
            if sha256_digest(existing) != digest:
                raise BlobIntegrityError(
                    f"existing blob path does not match digest {digest}"
                )
            return {
                "content_hash": digest,
                "byte_size": len(existing),
                "created": False,
                "storage": "local_content_addressed",
            }

        temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

        # Verify after the atomic rename rather than trusting only the source
        # buffer; corruption or unexpected filesystem behavior must fail closed.
        persisted = target.read_bytes()
        if sha256_digest(persisted) != digest:
            raise BlobIntegrityError(f"persisted blob failed integrity check: {digest}")
        return {
            "content_hash": digest,
            "byte_size": len(persisted),
            "created": True,
            "storage": "local_content_addressed",
        }

    def has(self, digest: str, *, verify: bool = False) -> bool:
        normalized = normalize_sha256_digest(digest)
        path = self._path(normalized)
        if not path.is_file():
            return False
        if verify:
            return sha256_digest(path.read_bytes()) == normalized
        return True

    def get(self, digest: str, *, max_bytes: int | None = None) -> bytes:
        normalized = normalize_sha256_digest(digest)
        path = self._path(normalized)
        if not path.is_file():
            raise KeyError(normalized)
        size = path.stat().st_size
        if max_bytes is not None and size > int(max_bytes):
            raise ValueError(
                f"blob is {size} bytes, above requested max_bytes={int(max_bytes)}"
            )
        value = path.read_bytes()
        if sha256_digest(value) != normalized:
            raise BlobIntegrityError(f"blob content no longer matches {normalized}")
        return value

    def delete_verified(self, digest: str) -> bool:
        """Delete one exact blob only after verifying its content identity.

        Callers must establish that no durable or in-flight retention reference
        still requires the bytes before invoking this low-level primitive.
        """
        normalized = normalize_sha256_digest(digest)
        path = self._path(normalized)
        if not path.is_file():
            return False
        value = path.read_bytes()
        if sha256_digest(value) != normalized:
            raise BlobIntegrityError(f"refusing to delete corrupt blob: {normalized}")
        path.unlink()
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            descriptor = None
        if descriptor is not None:
            try:
                os.fsync(descriptor)
            except OSError:
                pass
            finally:
                os.close(descriptor)
        return True

    def describe(self, digest: str) -> dict[str, Any]:
        normalized = normalize_sha256_digest(digest)
        path = self._path(normalized)
        if not path.is_file():
            raise KeyError(normalized)
        return {
            "content_hash": normalized,
            "byte_size": path.stat().st_size,
            "storage": "local_content_addressed",
            "integrity_verified": self.has(normalized, verify=True),
            "publication_rights_implied": False,
        }
