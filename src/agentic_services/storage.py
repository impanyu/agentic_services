from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import ClaimVerificationResult, EvidenceSnapshot


class VerificationStore:
    def __init__(self, database_path: Path, snapshot_directory: Path | None = None) -> None:
        self.database_path = database_path
        self.snapshot_directory = snapshot_directory or database_path.parent / "snapshots"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_directory.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS claim_verifications (
                    verification_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    content_path TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def get(self, verification_id: str) -> ClaimVerificationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM claim_verifications WHERE verification_id = ?",
                (verification_id,),
            ).fetchone()
        return ClaimVerificationResult.model_validate_json(row["result_json"]) if row else None

    def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[str, ClaimVerificationResult] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_hash, result_json
                FROM claim_verifications
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if not row:
            return None
        return row["request_hash"], ClaimVerificationResult.model_validate_json(row["result_json"])

    def save(
        self,
        *,
        result: ClaimVerificationResult,
        request_hash: str,
        request_json: str,
        idempotency_key: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO claim_verifications (
                    verification_id,
                    request_hash,
                    idempotency_key,
                    request_json,
                    result_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.verification_id,
                    request_hash,
                    idempotency_key,
                    request_json,
                    result.model_dump_json(by_alias=True),
                    result.observed_at.isoformat(),
                ),
            )

    def save_snapshot(self, snapshot: EvidenceSnapshot, content: bytes | None) -> None:
        content_path: str | None = None
        if content is not None:
            path = self.snapshot_directory / f"{snapshot.snapshot_id}.bin"
            temporary = self.snapshot_directory / f".{snapshot.snapshot_id}.tmp"
            temporary.write_bytes(content)
            temporary.replace(path)
            content_path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO evidence_snapshots (
                    snapshot_id,
                    metadata_json,
                    content_path,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.model_dump_json(by_alias=True),
                    content_path,
                    snapshot.retrieved_at.isoformat(),
                ),
            )

    def get_snapshot(self, snapshot_id: str) -> EvidenceSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM evidence_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        return EvidenceSnapshot.model_validate_json(row["metadata_json"]) if row else None

    def get_snapshot_content(self, snapshot_id: str) -> tuple[bytes, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json, content_path FROM evidence_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row or not row["content_path"]:
            return None
        metadata = EvidenceSnapshot.model_validate_json(row["metadata_json"])
        path = Path(row["content_path"])
        if not path.is_file() or path.parent.resolve() != self.snapshot_directory.resolve():
            return None
        return path.read_bytes(), metadata.content_type or "application/octet-stream"
