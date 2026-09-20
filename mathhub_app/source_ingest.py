from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAX_SOURCE_CHARS = 450_000
MAX_REMOTE_BYTES = 512_000
REMOTE_TIMEOUT_SECONDS = 15

_IMPORT_RE = re.compile(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)\s*$")
_DECL_RE = re.compile(
    r"^(?:private\s+|protected\s+|noncomputable\s+|unsafe\s+)*"
    r"(?P<kind>theorem|lemma|def)\s+(?P<name>[A-Za-z0-9_'.]+)"
)
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z0-9_.]+)\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+[A-Za-z0-9_.]+)?\s*$")


def _normalized_filename(value: str) -> str:
    raw = Path(str(value or "source.lean")).name.strip() or "source.lean"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-") or "source"
    if not safe.lower().endswith(".lean"):
        safe += ".lean"
    return safe[:120]


def _candidate_blocks(source: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    stack: list[str] = []
    starts: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines):
        namespace = _NAMESPACE_RE.match(line)
        if namespace:
            stack.append(namespace.group(1))
            continue
        if _END_RE.match(line):
            if stack:
                stack.pop()
            continue
        match = _DECL_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        qualified = name if "." in name or not stack else ".".join([*stack, name])
        starts.append((index, match.group("kind"), name, qualified))

    result: list[dict[str, Any]] = []
    for pos, (start, kind, name, qualified) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        proof_match = re.search(r":=\s*(by\b[\s\S]*)$", block)
        if proof_match:
            declaration = block[: proof_match.start()]
            marker = ":="
            proof = proof_match.group(1)
        else:
            declaration, marker, proof = block.partition(":=")
        standalone_declaration = declaration.strip()
        if qualified != name:
            decl_match = _DECL_RE.match(standalone_declaration)
            if decl_match:
                left, right = decl_match.span("name")
                standalone_declaration = (
                    standalone_declaration[:left] + qualified + standalone_declaration[right:]
                )
        result.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qualified,
                "line": start + 1,
                "declaration": standalone_declaration,
                "proof": proof.strip() if marker else "",
                "has_proof": bool(marker and proof.strip()),
                "source": block,
            }
        )
    return result


def parse_lean_source(source: str) -> dict[str, Any]:
    text = str(source or "")
    if not text.strip():
        raise ValueError("Lean source is empty")
    if len(text) > MAX_SOURCE_CHARS:
        raise ValueError(f"Lean source exceeds {MAX_SOURCE_CHARS} characters")
    return {
        "imports": list(dict.fromkeys(_IMPORT_RE.findall(text))) or ["Mathlib"],
        "candidates": _candidate_blocks(text),
    }


def _public_ip(host: str) -> None:
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve source host: {host}") from exc
    if not addresses:
        raise ValueError("source host did not resolve")
    for item in addresses:
        raw = item[4][0]
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("source host resolved to an invalid IP address") from exc
        if not address.is_global:
            raise ValueError("source URL must resolve only to globally routable Internet addresses")


def normalize_remote_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("source URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source URL must contain a public host and no embedded credentials")
    if parsed.hostname.lower() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _blob, ref = parts[:4]
            tail = "/".join(parts[4:])
            raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{tail}"
            parsed = urllib.parse.urlsplit(raw)
    _public_ip(parsed.hostname)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        checked = normalize_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, checked)


def fetch_remote_source(url: str) -> tuple[str, str, str]:
    checked = normalize_remote_url(url)
    opener = urllib.request.build_opener(_CheckedRedirect())
    request = urllib.request.Request(
        checked,
        headers={
            "Accept": "text/plain, text/x-lean, application/octet-stream;q=0.8",
            "User-Agent": "MathHub-Source-Ingest/1",
        },
    )
    try:
        with opener.open(request, timeout=REMOTE_TIMEOUT_SECONDS) as response:
            final_url = normalize_remote_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > MAX_REMOTE_BYTES:
                        raise ValueError(f"remote source exceeds {MAX_REMOTE_BYTES} bytes")
                except ValueError as exc:
                    if "exceeds" in str(exc):
                        raise
            data = response.read(MAX_REMOTE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"source URL returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"could not download source URL: {getattr(exc, 'reason', exc)}") from exc
    if len(data) > MAX_REMOTE_BYTES:
        raise ValueError(f"remote source exceeds {MAX_REMOTE_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("remote Lean source must be UTF-8 text") from exc
    filename = _normalized_filename(Path(urllib.parse.urlsplit(final_url).path).name or "remote.lean")
    return text, filename, final_url


def ingest_source(payload: dict[str, Any], *, data_root: Path) -> dict[str, Any]:
    source_text = payload.get("source_text")
    source_url = str(payload.get("source_url") or "").strip()
    if bool(source_text) == bool(source_url):
        raise ValueError("provide exactly one of source_text or source_url")

    origin = "upload"
    final_url = ""
    filename = _normalized_filename(str(payload.get("filename") or "source.lean"))
    if source_url:
        source_text, filename, final_url = fetch_remote_source(source_url)
        origin = "url"
    elif not isinstance(source_text, str):
        raise ValueError("source_text must be UTF-8 text")

    parsed = parse_lean_source(source_text)
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    source_id = "SRC-" + digest[:16].upper()
    target_dir = Path(data_root) / "source-ingest"
    target_dir.mkdir(parents=True, exist_ok=True)
    source_path = target_dir / f"{digest}.lean"
    metadata_path = target_dir / f"{digest}.json"
    if not source_path.exists():
        source_path.write_text(source_text, encoding="utf-8")
    metadata = {
        "source_id": source_id,
        "sha256": digest,
        "filename": filename,
        "origin": origin,
        "source_url": final_url or None,
        "imports": parsed["imports"],
        "candidate_count": len(parsed["candidates"]),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        **metadata,
        "candidates": parsed["candidates"],
    }
