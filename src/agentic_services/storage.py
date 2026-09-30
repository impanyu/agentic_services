from __future__ import annotations

import sqlite3
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ClaimVerificationResult, EvidenceSnapshot


class VerificationStore:
    DEFAULT_SERVICE_ID = "web-evidence"

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
                CREATE TABLE IF NOT EXISTS services (
                    service_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT OR IGNORE INTO services(
                    service_id,name,description,status,version,manifest_url,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    self.DEFAULT_SERVICE_ID,
                    "Web Evidence",
                    "Current web claim verification with cited evidence and snapshots.",
                    "active",
                    "0.3.0",
                    "/.well-known/agent-service.json",
                    now,
                    now,
                ),
            )
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
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT,
                    api_key_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL DEFAULT 'web-evidence',
                    customer_id TEXT,
                    customer_reference TEXT,
                    tier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price_microusd INTEGER NOT NULL,
                    payment_protocol TEXT NOT NULL,
                    order_token_hash TEXT,
                    request_hash TEXT,
                    verification_id TEXT,
                    provider_response_id TEXT,
                    model TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    web_search_calls INTEGER NOT NULL DEFAULT 0,
                    model_cost_microusd INTEGER NOT NULL DEFAULT 0,
                    search_cost_microusd INTEGER NOT NULL DEFAULT 0,
                    total_cost_microusd INTEGER NOT NULL DEFAULT 0,
                    receipt_id TEXT,
                    receipt_json TEXT,
                    receipt_signature TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
                )
                """
            )
            order_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            if "service_id" not in order_columns:
                connection.execute(
                    "ALTER TABLE orders ADD COLUMN service_id TEXT NOT NULL DEFAULT 'web-evidence'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS orders_customer_created ON orders(customer_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS orders_created ON orders(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS orders_service_created ON orders(service_id, created_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    entry_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount_microusd INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quotes (
                    quote_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL DEFAULT 'web-evidence',
                    tier TEXT NOT NULL,
                    amount_microusd INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            quote_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(quotes)").fetchall()
            }
            if "service_id" not in quote_columns:
                connection.execute(
                    "ALTER TABLE quotes ADD COLUMN service_id TEXT NOT NULL DEFAULT 'web-evidence'"
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

    @staticmethod
    def hash_api_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_customer(self, *, name: str, email: str | None = None) -> tuple[dict[str, Any], str]:
        customer_id = f"cus_{uuid.uuid4().hex}"
        api_key = f"ask_{secrets.token_urlsafe(32)}"
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO customers(customer_id,name,email,api_key_hash,status,created_at) VALUES(?,?,?,?,?,?)",
                (customer_id, name, email, self.hash_api_key(api_key), "active", created_at),
            )
        return {
            "customerId": customer_id,
            "name": name,
            "email": email,
            "status": "active",
            "createdAt": created_at,
        }, api_key

    def customer_for_key(self, api_key: str | None) -> dict[str, Any] | None:
        if not api_key:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT customer_id,name,email,status,created_at FROM customers WHERE api_key_hash=?",
                (self.hash_api_key(api_key),),
            ).fetchone()
        return dict(row) if row and row["status"] == "active" else None

    def list_customers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.customer_id,c.name,c.email,c.status,c.created_at,
                       COUNT(o.order_id) AS order_count,
                       COALESCE(SUM(CASE WHEN o.status='completed' THEN o.price_microusd ELSE 0 END),0) AS revenue_microusd
                FROM customers c LEFT JOIN orders o ON o.customer_id=c.customer_id
                GROUP BY c.customer_id ORDER BY c.created_at DESC
                """
            ).fetchall()
        return [self._customer_row(row) for row in rows]

    def create_order(
        self,
        *,
        order_id: str,
        tier: str,
        price_microusd: int,
        payment_protocol: str,
        order_token_hash: str | None,
        request_hash: str,
        customer_key: str | None,
        customer_reference: str | None,
        service_id: str = DEFAULT_SERVICE_ID,
    ) -> None:
        customer = self.customer_for_key(customer_key)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO orders(order_id,service_id,customer_id,customer_reference,tier,status,currency,
                    price_microusd,payment_protocol,order_token_hash,request_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    order_id,
                    service_id,
                    customer["customer_id"] if customer else None,
                    customer_reference,
                    tier,
                    "processing",
                    "USD",
                    price_microusd,
                    payment_protocol,
                    order_token_hash,
                    request_hash,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def complete_order(self, *, order_id: str, values: dict[str, Any], receipt: dict[str, Any], signature: str) -> None:
        completed_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE orders SET status='completed',verification_id=?,provider_response_id=?,model=?,
                  input_tokens=?,cached_input_tokens=?,output_tokens=?,web_search_calls=?,
                  model_cost_microusd=?,search_cost_microusd=?,total_cost_microusd=?,
                  receipt_id=?,receipt_json=?,receipt_signature=?,completed_at=? WHERE order_id=?
                """,
                (
                    values["verification_id"], values["provider_response_id"], values["model"],
                    values["input_tokens"], values["cached_input_tokens"], values["output_tokens"],
                    values["web_search_calls"], values["model_cost_microusd"],
                    values["search_cost_microusd"], values["total_cost_microusd"],
                    receipt["receiptId"], json.dumps(receipt, separators=(",", ":"), sort_keys=True),
                    signature, completed_at, order_id,
                ),
            )
            order = connection.execute("SELECT price_microusd FROM orders WHERE order_id=?", (order_id,)).fetchone()
            entries = [
                ("revenue", "customer_payment", int(order["price_microusd"])),
                ("cost", "openai_model", int(values["model_cost_microusd"])),
                ("cost", "openai_web_search", int(values["search_cost_microusd"])),
            ]
            for kind, category, amount in entries:
                if amount <= 0:
                    continue
                connection.execute(
                    "INSERT INTO ledger_entries VALUES(?,?,?,?,?,?,?)",
                    (f"le_{uuid.uuid4().hex}", order_id, kind, category, amount, completed_at, "{}"),
                )

    def fail_order(self, order_id: str, error_code: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE orders SET status='failed',error_code=?,completed_at=? WHERE order_id=?",
                (error_code, datetime.now(UTC).isoformat(), order_id),
            )

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return self._order_row(row) if row else None

    def get_order_for_customer(self, order_id: str, customer_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE order_id=? AND customer_id=?", (order_id, customer_id)
            ).fetchone()
        return self._order_row(row, customer_view=True) if row else None

    def get_order_with_token(self, order_id: str, token: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row or not row["order_token_hash"]:
            return None
        if not secrets.compare_digest(row["order_token_hash"], self.hash_api_key(token)):
            return None
        return self._order_row(row, customer_view=True)

    def list_orders(
        self,
        *,
        customer_id: str | None = None,
        service_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM orders"
        params: list[Any] = []
        filters: list[str] = []
        if customer_id:
            filters.append("customer_id=?")
            params.append(customer_id)
        if service_id:
            filters.append("service_id=?")
            params.append(service_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._order_row(row, customer_view=customer_id is not None) for row in rows]

    def count_orders(
        self, *, customer_id: str | None = None, service_id: str | None = None
    ) -> int:
        query = "SELECT COUNT(*) FROM orders"
        params: list[Any] = []
        filters: list[str] = []
        if customer_id:
            filters.append("customer_id=?")
            params.append(customer_id)
        if service_id:
            filters.append("service_id=?")
            params.append(service_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        with self._connect() as connection:
            return int(connection.execute(query, params).fetchone()[0])

    def admin_summary(
        self, since: str | None = None, service_id: str | None = None
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: list[Any] = []
        if since:
            filters.append("created_at>=?")
            params.append(since)
        if service_id:
            filters.append("service_id=?")
            params.append(service_id)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self._connect() as connection:
            totals = connection.execute(
                f"""
                SELECT COUNT(*) order_count,
                  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed_count,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed_count,
                  COALESCE(SUM(CASE WHEN status='completed' THEN price_microusd ELSE 0 END),0) revenue_microusd,
                  COALESCE(SUM(CASE WHEN status='completed' THEN total_cost_microusd ELSE 0 END),0) cost_microusd,
                  COALESCE(SUM(web_search_calls),0) web_search_calls,
                  COALESCE(SUM(input_tokens),0) input_tokens,
                  COALESCE(SUM(output_tokens),0) output_tokens
                FROM orders{where}
                """, tuple(params),
            ).fetchone()
            tiers = connection.execute(
                f"""
                SELECT tier,COUNT(*) order_count,
                  COALESCE(SUM(CASE WHEN status='completed' THEN price_microusd ELSE 0 END),0) revenue_microusd,
                  COALESCE(SUM(CASE WHEN status='completed' THEN total_cost_microusd ELSE 0 END),0) cost_microusd
                FROM orders{where} GROUP BY tier ORDER BY revenue_microusd DESC
                """, tuple(params),
            ).fetchall()
            services = connection.execute(
                f"""
                SELECT service_id,COUNT(*) order_count,
                  COALESCE(SUM(CASE WHEN status='completed' THEN price_microusd ELSE 0 END),0) revenue_microusd,
                  COALESCE(SUM(CASE WHEN status='completed' THEN total_cost_microusd ELSE 0 END),0) cost_microusd
                FROM orders{where} GROUP BY service_id ORDER BY revenue_microusd DESC
                """, tuple(params),
            ).fetchall()
        revenue = int(totals["revenue_microusd"])
        cost = int(totals["cost_microusd"])
        return {
            "orderCount": int(totals["order_count"]),
            "completedCount": int(totals["completed_count"] or 0),
            "failedCount": int(totals["failed_count"] or 0),
            "revenueMicrousd": revenue,
            "costMicrousd": cost,
            "grossProfitMicrousd": revenue - cost,
            "grossMargin": round((revenue - cost) / revenue, 4) if revenue else None,
            "webSearchCalls": int(totals["web_search_calls"]),
            "inputTokens": int(totals["input_tokens"]),
            "outputTokens": int(totals["output_tokens"]),
            "byTier": [
                {
                    "tier": row["tier"], "orderCount": row["order_count"],
                    "revenueMicrousd": row["revenue_microusd"], "costMicrousd": row["cost_microusd"],
                    "grossProfitMicrousd": row["revenue_microusd"] - row["cost_microusd"],
                }
                for row in tiers
            ],
            "byService": [
                {
                    "serviceId": row["service_id"], "orderCount": row["order_count"],
                    "revenueMicrousd": row["revenue_microusd"], "costMicrousd": row["cost_microusd"],
                    "grossProfitMicrousd": row["revenue_microusd"] - row["cost_microusd"],
                }
                for row in services
            ],
        }

    def list_services(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM services ORDER BY created_at"
            ).fetchall()
        return [
            {
                "serviceId": row["service_id"],
                "name": row["name"],
                "description": row["description"],
                "status": row["status"],
                "version": row["version"],
                "manifestUrl": row["manifest_url"],
            }
            for row in rows
        ]

    def create_quote(
        self,
        *,
        tier: str,
        amount_microusd: int,
        expires_at: str,
        service_id: str = DEFAULT_SERVICE_ID,
    ) -> dict[str, Any]:
        quote_id = f"quo_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quotes(
                    quote_id,service_id,tier,amount_microusd,currency,expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (quote_id, service_id, tier, amount_microusd, "USD", expires_at, created_at),
            )
        return {"quoteId": quote_id, "serviceId": service_id, "tier": tier, "amountMicrousd": amount_microusd, "currency": "USD", "expiresAt": expires_at, "createdAt": created_at}

    @staticmethod
    def _customer_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "customerId": row["customer_id"], "name": row["name"], "email": row["email"],
            "status": row["status"], "createdAt": row["created_at"],
            "orderCount": int(row["order_count"]), "revenueMicrousd": int(row["revenue_microusd"]),
        }

    @staticmethod
    def _order_row(row: sqlite3.Row, customer_view: bool = False) -> dict[str, Any]:
        result = {
            "orderId": row["order_id"], "serviceId": row["service_id"], "customerId": row["customer_id"],
            "customerReference": row["customer_reference"], "tier": row["tier"],
            "status": row["status"], "currency": row["currency"],
            "amountMicrousd": row["price_microusd"], "paymentProtocol": row["payment_protocol"],
            "verificationId": row["verification_id"], "createdAt": row["created_at"],
            "completedAt": row["completed_at"], "receiptId": row["receipt_id"],
        }
        if row["receipt_json"]:
            result["receipt"] = {**json.loads(row["receipt_json"]), "signature": row["receipt_signature"]}
        if not customer_view:
            cost = int(row["total_cost_microusd"])
            revenue = int(row["price_microusd"]) if row["status"] == "completed" else 0
            result.update({
                "providerResponseId": row["provider_response_id"], "model": row["model"],
                "inputTokens": row["input_tokens"], "cachedInputTokens": row["cached_input_tokens"],
                "outputTokens": row["output_tokens"], "webSearchCalls": row["web_search_calls"],
                "modelCostMicrousd": row["model_cost_microusd"],
                "searchCostMicrousd": row["search_cost_microusd"], "totalCostMicrousd": cost,
                "grossProfitMicrousd": revenue - cost, "errorCode": row["error_code"],
            })
        return result
