from __future__ import annotations

import sqlite3
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
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
                    "0.3.1",
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS contact_messages (
                    message_id TEXT PRIMARY KEY,
                    service_id TEXT,
                    sender_name TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    ip_hash TEXT NOT NULL,
                    user_agent TEXT,
                    delivery_status TEXT NOT NULL,
                    delivery_error TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS contact_messages_ip_created ON contact_messages(ip_hash, created_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS support_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    service_id TEXT,
                    customer_id TEXT,
                    order_id TEXT,
                    subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    access_token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            support_ticket_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(support_tickets)").fetchall()
            }
            if "resolved_at" not in support_ticket_columns:
                connection.execute("ALTER TABLE support_tickets ADD COLUMN resolved_at TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS support_messages (
                    message_id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    author_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(ticket_id) REFERENCES support_tickets(ticket_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS support_tickets_created ON support_tickets(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS support_messages_ticket_created ON support_messages(ticket_id, created_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS status_components (
                    component_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for component in (
                ("web-evidence-api", "Web Evidence API", "HTTP claim verification API", 10),
                ("web-evidence-mcp", "Web Evidence MCP", "Model Context Protocol endpoint", 20),
                ("web-evidence-a2a", "Web Evidence A2A", "Agent-to-Agent endpoint", 30),
                ("payments", "Payment rails", "x402, MPP, USDC, and card payments", 40),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO status_components(
                        component_id,name,description,status,sort_order,updated_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (*component[:3], "operational", component[3], now),
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS status_incidents (
                    incident_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    affected_components_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS status_subscribers (
                    subscriber_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    target TEXT NOT NULL,
                    verification_email TEXT NOT NULL,
                    verification_token_hash TEXT NOT NULL UNIQUE,
                    signing_secret TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    verified_at TEXT
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

    def count_recent_contact_messages(self, *, ip_hash: str, since: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM contact_messages WHERE ip_hash=? AND created_at>=?",
                (ip_hash, since.isoformat()),
            ).fetchone()
        return int(row["count"])

    def create_contact_message(
        self,
        *,
        sender_name: str,
        sender_email: str,
        subject: str,
        message_text: str,
        ip_hash: str,
        user_agent: str | None,
        service_id: str | None,
    ) -> dict[str, str | None]:
        message_id = f"msg_{uuid.uuid4().hex}"
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO contact_messages(
                    message_id,service_id,sender_name,sender_email,subject,message_text,
                    ip_hash,user_agent,delivery_status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    message_id,
                    service_id,
                    sender_name,
                    sender_email,
                    subject,
                    message_text,
                    ip_hash,
                    user_agent,
                    "pending",
                    created_at,
                ),
            )
        return {"messageId": message_id, "status": "pending", "createdAt": created_at}

    def update_contact_delivery(
        self, *, message_id: str, status: str, error: str | None = None
    ) -> None:
        delivered_at = datetime.now(UTC).isoformat() if status == "sent" else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE contact_messages
                SET delivery_status=?, delivery_error=?, delivered_at=?
                WHERE message_id=?
                """,
                (status, error, delivered_at, message_id),
            )

    def list_contact_messages(
        self,
        *,
        limit: int,
        offset: int,
        service_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where = "WHERE service_id=?" if service_id else ""
        parameters: tuple[Any, ...] = (service_id, limit, offset) if service_id else (limit, offset)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT message_id,service_id,sender_name,sender_email,subject,message_text,
                       delivery_status,created_at,delivered_at
                FROM contact_messages {where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [
            {
                "messageId": row["message_id"],
                "serviceId": row["service_id"],
                "name": row["sender_name"],
                "email": row["sender_email"],
                "subject": row["subject"],
                "message": row["message_text"],
                "deliveryStatus": row["delivery_status"],
                "createdAt": row["created_at"],
                "deliveredAt": row["delivered_at"],
            }
            for row in rows
        ]

    def count_contact_messages(self, *, service_id: str | None = None) -> int:
        where = "WHERE service_id=?" if service_id else ""
        parameters: tuple[Any, ...] = (service_id,) if service_id else ()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM contact_messages {where}", parameters
            ).fetchone()
        return int(row["count"])

    def create_support_ticket(
        self,
        *,
        subject: str,
        message_text: str,
        service_id: str | None,
        customer_id: str | None,
        order_id: str | None,
        priority: str,
    ) -> tuple[dict[str, Any], str]:
        ticket_id = f"tkt_{uuid.uuid4().hex}"
        message_id = f"tmsg_{uuid.uuid4().hex}"
        access_token = f"tsk_{secrets.token_urlsafe(32)}"
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO support_tickets(
                    ticket_id,service_id,customer_id,order_id,subject,status,priority,
                    access_token_hash,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ticket_id, service_id, customer_id, order_id, subject, "open", priority,
                    self.hash_api_key(access_token), now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO support_messages(message_id,ticket_id,author_type,message_text,created_at)
                VALUES(?,?,?,?,?)
                """,
                (message_id, ticket_id, "customer", message_text, now),
            )
        ticket = self.get_support_ticket(ticket_id)
        if ticket is None:
            raise RuntimeError("Support ticket could not be created")
        return ticket, access_token

    def get_support_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT ticket_id,service_id,customer_id,order_id,subject,status,priority,
                       created_at,updated_at,resolved_at
                FROM support_tickets WHERE ticket_id=?
                """,
                (ticket_id,),
            ).fetchone()
            if not row:
                return None
            messages = connection.execute(
                """
                SELECT message_id,author_type,message_text,created_at
                FROM support_messages WHERE ticket_id=? ORDER BY created_at
                """,
                (ticket_id,),
            ).fetchall()
        return {
            "ticketId": row["ticket_id"], "serviceId": row["service_id"],
            "customerId": row["customer_id"], "orderId": row["order_id"],
            "subject": row["subject"], "status": row["status"], "priority": row["priority"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "resolvedAt": row["resolved_at"],
            "messages": [
                {"messageId": item["message_id"], "authorType": item["author_type"],
                 "message": item["message_text"], "createdAt": item["created_at"]}
                for item in messages
            ],
        }

    def support_ticket_for_token(self, ticket_id: str, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT access_token_hash FROM support_tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
        if not row or not secrets.compare_digest(row["access_token_hash"], self.hash_api_key(token)):
            return None
        return self.get_support_ticket(ticket_id)

    def add_support_message(self, *, ticket_id: str, author_type: str, message_text: str) -> dict[str, Any]:
        message_id = f"tmsg_{uuid.uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO support_messages(message_id,ticket_id,author_type,message_text,created_at)
                VALUES(?,?,?,?,?)
                """,
                (message_id, ticket_id, author_type, message_text, now),
            )
            connection.execute(
                "UPDATE support_tickets SET updated_at=?,status=CASE WHEN ?='customer' AND status='resolved' THEN 'open' ELSE status END WHERE ticket_id=?",
                (now, author_type, ticket_id),
            )
        return {"messageId": message_id, "authorType": author_type, "message": message_text, "createdAt": now}

    def list_support_tickets(self, *, limit: int, offset: int, status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE status=?" if status else ""
        parameters: tuple[Any, ...] = (status, limit, offset) if status else (limit, offset)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT ticket_id FROM support_tickets {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [ticket for row in rows if (ticket := self.get_support_ticket(row["ticket_id"]))]

    def count_support_tickets(self, *, status: str | None = None) -> int:
        where = "WHERE status=?" if status else ""
        parameters: tuple[Any, ...] = (status,) if status else ()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM support_tickets {where}", parameters
            ).fetchone()
        return int(row["count"])

    def update_support_ticket(self, *, ticket_id: str, status: str, message_text: str | None) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        resolved_at = now if status == "resolved" else None
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE support_tickets SET status=?,updated_at=?,resolved_at=? WHERE ticket_id=?",
                (status, now, resolved_at, ticket_id),
            )
        if result.rowcount == 0:
            return None
        if message_text:
            self.add_support_message(ticket_id=ticket_id, author_type="support", message_text=message_text)
        return self.get_support_ticket(ticket_id)

    def status_document(self) -> dict[str, Any]:
        with self._connect() as connection:
            components = connection.execute(
                "SELECT component_id,name,description,status,updated_at FROM status_components ORDER BY sort_order"
            ).fetchall()
            incidents = connection.execute(
                """
                SELECT * FROM status_incidents
                WHERE status!='resolved' OR resolved_at>=?
                ORDER BY created_at DESC LIMIT 50
                """,
                ((datetime.now(UTC) - timedelta(days=30)).isoformat(),),
            ).fetchall()
        component_items = [
            {"componentId": row["component_id"], "name": row["name"],
             "description": row["description"], "status": row["status"], "updatedAt": row["updated_at"]}
            for row in components
        ]
        active = [row for row in incidents if row["status"] != "resolved"]
        aggregate = "operational"
        if any(row["severity"] == "major" for row in active): aggregate = "downtime"
        elif active: aggregate = "degraded"
        return {
            "page": {"name": "Dream Workshop Status", "url": "https://status.aisoup.net", "aggregateStatus": aggregate,
                     "updatedAt": max((row["updated_at"] for row in components), default=datetime.now(UTC).isoformat())},
            "components": component_items,
            "incidents": [self._incident_row(row) for row in incidents],
        }

    def create_status_incident(self, *, title: str, message_text: str, severity: str, component_ids: list[str]) -> dict[str, Any]:
        incident_id = f"inc_{uuid.uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO status_incidents(incident_id,title,message_text,severity,status,affected_components_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (incident_id, title, message_text, severity, "investigating", json.dumps(component_ids), now, now),
            )
            for component_id in component_ids:
                connection.execute(
                    "UPDATE status_components SET status=?,updated_at=? WHERE component_id=?",
                    ("downtime" if severity == "major" else "degraded", now, component_id),
                )
        return self.get_status_incident(incident_id) or {}

    def get_status_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM status_incidents WHERE incident_id=?", (incident_id,)).fetchone()
        return self._incident_row(row) if row else None

    def update_status_incident(self, *, incident_id: str, status: str, message_text: str) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        resolved_at = now if status == "resolved" else None
        with self._connect() as connection:
            row = connection.execute("SELECT affected_components_json FROM status_incidents WHERE incident_id=?", (incident_id,)).fetchone()
            if not row: return None
            connection.execute(
                "UPDATE status_incidents SET status=?,message_text=?,updated_at=?,resolved_at=? WHERE incident_id=?",
                (status, message_text, now, resolved_at, incident_id),
            )
            if status == "resolved":
                for component_id in json.loads(row["affected_components_json"]):
                    connection.execute("UPDATE status_components SET status='operational',updated_at=? WHERE component_id=?", (now, component_id))
        return self.get_status_incident(incident_id)

    def create_status_subscriber(self, *, channel: str, target: str, verification_email: str) -> tuple[dict[str, Any], str]:
        subscriber_id = f"sub_{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        signing_secret = secrets.token_urlsafe(32) if channel == "webhook" else None
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO status_subscribers(subscriber_id,channel,target,verification_email,verification_token_hash,signing_secret,status,created_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (subscriber_id, channel, target, verification_email, self.hash_api_key(token), signing_secret, "pending", now),
            )
        return {"subscriberId": subscriber_id, "channel": channel, "status": "pending", "signingSecret": signing_secret}, token

    def verify_status_subscriber(self, token: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE status_subscribers SET status='active',verified_at=? WHERE verification_token_hash=? AND status='pending'",
                (now, self.hash_api_key(token)),
            )
        return result.rowcount > 0

    def active_status_subscribers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT subscriber_id,channel,target,verification_email,signing_secret FROM status_subscribers WHERE status='active'"
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _incident_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"incidentId": row["incident_id"], "title": row["title"], "message": row["message_text"],
                "severity": row["severity"], "status": row["status"],
                "affectedComponents": json.loads(row["affected_components_json"]),
                "createdAt": row["created_at"], "updatedAt": row["updated_at"], "resolvedAt": row["resolved_at"]}

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
