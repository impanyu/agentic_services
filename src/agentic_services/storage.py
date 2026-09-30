from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import ClaimVerificationResult


class VerificationStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
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
