# Agentic Services

Agentic Services is a service matrix for autonomous agents. Each service exposes a narrow, valuable information capability that an external agent can discover, evaluate, purchase, and call without manual account setup.

The platform treats APIs, MCP tools, agent-to-agent services, software runtimes, and databases as different transports for the same commercial object: an **agent service**.

## Product contract

Every published service must provide:

1. A machine-readable description of its capabilities and input/output schemas.
2. At least one callable transport: HTTP, MCP, or A2A.
3. A deterministic price or a machine-readable quote flow.
4. At least one automated payment method.
5. Provenance, freshness, service-level, and policy metadata.
6. An idempotent execution path and a verifiable receipt.

The canonical public manifest lives at:

```text
https://<service-host>/.well-known/agent-service.json
```

The same manifest can be indexed by the platform registry and exported to compatible discovery networks.

## Repository layout

```text
docs/                         Product and system design
examples/                     Example service manifests
schemas/                      Versioned protocol schemas
services/                     Product-specific documentation
src/agentic_services/         Runnable gateway and Web Evidence API
tests/                        API contract and safety tests
```

The initial protocol is defined by [`schemas/service-manifest.schema.json`](schemas/service-manifest.schema.json). An illustrative service is in [`examples/weather-risk.service.json`](examples/weather-risk.service.json).

## Architecture

The platform has four layers:

- **Service layer** — focused information products owned by individual service modules.
- **Gateway layer** — identity, quotes, payment verification, rate limits, and receipts.
- **Registry layer** — capability search, health, reputation, and machine-readable manifests.
- **Settlement layer** — adapters for pay-per-call, credits, and subscriptions.

See [`docs/architecture.md`](docs/architecture.md) for the execution flow and [`docs/roadmap.md`](docs/roadmap.md) for the build sequence.

## Design principles

- Protocol-first: an agent can integrate from schemas without reading prose.
- Narrow services: each service owns a small domain and returns a useful result, not raw data alone.
- Payment-neutral core: commercial terms are stable while payment rails remain replaceable.
- Verifiable delivery: every paid execution produces a receipt tied to the request, price, and result.
- Safe autonomy: budgets, expiry, replay protection, and idempotency are enforced in deterministic code.
- Federated discovery: the platform registry is useful but is not the only way to find a service.

## Run Web Evidence locally

The first runnable service is **Web Evidence**, a structured claim-verification API backed by the OpenAI Responses API and hosted web search.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env.local
# Add OPENAI_API_KEY to .env.local
.venv/bin/agentic-services
```

The API starts at `http://localhost:8000`. Its main endpoints are:

- `POST /v1/claims/verify/quick` — $0.02 quick verification.
- `POST /v1/claims/verify` — $0.05 standard verification.
- `POST /v1/claims/verify/deep` — $0.12 deep verification.
- `POST /v1/claims/verify/research` — $0.25 research-grade verification.
- `GET /v1/claims/verifications/{verification_id}` — retrieve the immutable result.
- `GET /v1/url-snapshots/{snapshot_id}` — retrieve snapshot status and content hashes.
- `GET /v1/url-snapshots/{snapshot_id}/content` — retrieve the exact captured response bytes.
- `GET /.well-known/agent-service.json` — discover the service and its schemas.
- `GET /.well-known/x402` — discover x402-payable resource URLs.
- `POST /mcp` — MCP Streamable HTTP server with one free discovery tool and four x402-paid verification tools.
- `GET /.well-known/mcp/server.json` — MCP Registry metadata.
- `POST /a2a` — A2A 1.0 JSON-RPC `SendMessage`, paid at the Standard tier.
- `GET /.well-known/agent-card.json` — A2A Agent Card.
- `GET /openapi.json` — inspect the complete HTTP contract.
- `GET /v1/services` — list every service in the platform catalog.
- `GET /llms.txt` — read concise agent integration instructions.
- `GET /` — human-readable landing page with structured data; `robots.txt` and `sitemap.xml` support web indexing.
- `POST /v1/quotes` — create a 15-minute machine-readable tier quote.
- `GET /v1/orders/{order_id}` — retrieve one paid order and signed receipt with its one-time order token.
- `GET /v1/customer/orders` — list a registered customer's orders with `X-Agentic-Customer-Key`.
- `POST /v1/receipts/{order_id}/verify` — verify the server signature on an issued receipt.
- `GET /admin` — private commerce dashboard for revenue, OpenAI cost, gross profit, and individual orders.
- `GET /v1/admin/services` — list services available to the commerce dashboard.
- `GET /v1/admin/summary`, `GET /v1/admin/orders` — dashboard APIs authenticated with `X-Admin-Key`; both support platform-wide reporting and `serviceId` filtering.
- `POST /v1/admin/customers` — issue a customer API key; plaintext is returned once and only its SHA-256 hash is stored.

Example request:

```bash
curl http://localhost:8000/v1/claims/verify \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-claim-1' \
  -d '{
    "claim": "OpenAI publishes an official Responses API reference.",
    "sourcePolicy": "official_only",
    "allowedDomains": ["openai.com"],
    "minimumSources": 1
  }'
```

See [`docs/web-evidence-api.md`](docs/web-evidence-api.md) for request semantics, evidence guarantees, and the planned paid-service endpoints.

For the production Docker Compose deployment at `api.aisoup.net`, follow [`deploy/google-cloud-vm.md`](deploy/google-cloud-vm.md). The public gateway accepts both x402 and MPP payments in Base USDC; the Python service remains private behind an internal Bearer credential.

## Live discovery

Web Evidence is published through the following public discovery surfaces:

- [Official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers/io.github.impanyu%2Fweb-evidence/versions/0.3.0) as `io.github.impanyu/web-evidence`.
- [OpenX402 Bazaar](https://facilitator.openx402.ai/discovery/resources?type=mcp) as the paid MCP resource `mcp://api.aisoup.net/verify_claim_quick`.
- [x402Scan](https://www.x402scan.com/server/131c08a0-027b-4f28-afe6-2a72fdbac7dc) with all four paid HTTP tiers and both public snapshot routes.
- [MPPScan](https://www.mppscan.com/server/b915b7bda6517cd0e47f1cfaca657b4a4f7b268093117889d34b31bad1915664) with the same six HTTP routes.
- [Global A2A Registry](https://www.a2a-registry.org/agent/net.aisoup.web_evidence) and the [open-source A2A Registry](https://a2aregistry.org) via the public A2A 1.0 Agent Card.

The repository also carries `server.json` for MCP Registry publication and `glama.json` for downstream MCP directory indexing. The landing page publishes Schema.org service metadata, `robots.txt`, `sitemap.xml`, OpenAPI, `llms.txt`, and well-known manifests for independent crawlers.

## Status

The repository contains the v0 protocol, a runnable tiered Web Evidence service, full provider-source provenance, URL snapshots with raw and normalized SHA-256 hashes, SQLite persistence, machine-readable discovery, and an x402/MPP dual-protocol payment gateway. Each paid HTTP, MCP, or A2A execution creates an order, signed receipt, and detailed revenue/cost ledger entry. The admin dashboard reports per-order OpenAI token and Web Search costs, gross profit, and margins. Production payment settlement and public MCP, A2A, x402, and MPP directory discovery have been verified. The next milestone is additional evidence operations and ongoing directory health monitoring.
