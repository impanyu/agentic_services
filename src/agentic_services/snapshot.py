from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import EvidenceSnapshot, SnapshotStatus


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs can be snapshotted")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    addresses = {
        ipaddress.ip_address(item[4][0])
        for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    }
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("The URL resolves to a non-public network address")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _normalized_content(raw: bytes, content_type: str) -> bytes | None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        parser.feed(raw.decode("utf-8", errors="replace"))
        text = " ".join(" ".join(parser.parts).split())
        return text.encode("utf-8")
    if media_type == "application/json":
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if media_type.startswith("text/") or media_type.endswith("+xml") or media_type in {
        "application/xml",
    }:
        return " ".join(raw.decode("utf-8", errors="replace").split()).encode("utf-8")
    return None


@dataclass(frozen=True)
class SnapshotCapture:
    metadata: EvidenceSnapshot
    content: bytes | None


class WebSnapshotter:
    def __init__(self, *, timeout_seconds: int = 12, max_bytes: int = 5_000_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def capture(self, url: str) -> SnapshotCapture:
        snapshot_id = f"snap_{uuid.uuid4().hex}"
        retrieved_at = datetime.now(UTC)
        try:
            _validate_public_url(url)
            opener = build_opener(_SafeRedirectHandler())
            request = Request(
                url,
                headers={
                    "User-Agent": "AgenticServices-WebEvidence/0.2 (+https://api.aisoup.net)",
                    "Accept": "text/html,application/xhtml+xml,application/json,application/pdf,text/plain;q=0.8,*/*;q=0.2",
                },
            )
            with opener.open(request, timeout=self.timeout_seconds) as response:
                final_url = response.geturl()
                _validate_public_url(final_url)
                content_type = response.headers.get_content_type()
                allowed = content_type.startswith("text/") or content_type in {
                    "application/json",
                    "application/xml",
                    "application/xhtml+xml",
                    "application/pdf",
                } or content_type.endswith("+xml")
                if not allowed:
                    return self._failure(
                        snapshot_id,
                        url,
                        retrieved_at,
                        SnapshotStatus.UNSUPPORTED,
                        f"Unsupported content type: {content_type}",
                        final_url=final_url,
                        http_status=response.status,
                        content_type=content_type,
                    )
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    return self._failure(
                        snapshot_id,
                        url,
                        retrieved_at,
                        SnapshotStatus.TOO_LARGE,
                        f"Response exceeded {self.max_bytes} bytes",
                        final_url=final_url,
                        http_status=response.status,
                        content_type=content_type,
                    )
                normalized = _normalized_content(raw, content_type)
                metadata = EvidenceSnapshot(
                    snapshot_id=snapshot_id,
                    requested_url=url,
                    final_url=final_url,
                    retrieved_at=retrieved_at,
                    status=SnapshotStatus.CAPTURED,
                    http_status=response.status,
                    content_type=content_type,
                    content_length=len(raw),
                    raw_sha256=hashlib.sha256(raw).hexdigest(),
                    normalized_sha256=(
                        hashlib.sha256(normalized).hexdigest() if normalized is not None else None
                    ),
                )
                return SnapshotCapture(metadata=metadata, content=raw)
        except ValueError as error:
            return self._failure(
                snapshot_id, url, retrieved_at, SnapshotStatus.BLOCKED, str(error)
            )
        except HTTPError as error:
            return self._failure(
                snapshot_id,
                url,
                retrieved_at,
                SnapshotStatus.FAILED,
                f"HTTP {error.code}",
                final_url=error.geturl(),
                http_status=error.code,
                content_type=error.headers.get_content_type() if error.headers else None,
            )
        except (URLError, TimeoutError, OSError) as error:
            return self._failure(
                snapshot_id,
                url,
                retrieved_at,
                SnapshotStatus.FAILED,
                f"{type(error).__name__}: {error}",
            )

    @staticmethod
    def _failure(
        snapshot_id: str,
        requested_url: str,
        retrieved_at: datetime,
        status: SnapshotStatus,
        reason: str,
        *,
        final_url: str | None = None,
        http_status: int | None = None,
        content_type: str | None = None,
    ) -> SnapshotCapture:
        return SnapshotCapture(
            metadata=EvidenceSnapshot(
                snapshot_id=snapshot_id,
                requested_url=requested_url,
                final_url=final_url,
                retrieved_at=retrieved_at,
                status=status,
                http_status=http_status,
                content_type=content_type,
                failure_reason=reason[:500],
            ),
            content=None,
        )

    def capture_many(self, urls: Iterable[str]) -> list[SnapshotCapture]:
        ordered_urls = list(urls)
        if not ordered_urls:
            return []
        with ThreadPoolExecutor(max_workers=min(4, len(ordered_urls))) as executor:
            return list(executor.map(self.capture, ordered_urls))
