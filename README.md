# Agentic Services

Agentic Services is a multi-service platform for human users, autonomous agents, or both. Each service owns a focused capability and can be sold independently through the appropriate user-facing and/or machine-facing channel.

Services belong to one of three product categories:

- **Human-only:** designed for people to use through a UI; no agent-callable interface is required.
- **Agent-only:** designed for autonomous agents to discover, purchase, and invoke through a machine interface; an informational product page is not a human-use UI.
- **Human-and-agent:** provides both a usable human UI and a machine service interface for the same underlying capability.

The category describes intended users, not whether a website or API happens to exist. APIs, MCP tools, and A2A services are possible machine transports; a human UI is a separate product surface.

## Product contract

Every published service has a stable identity, its own offer and delivery contract, a payment path, and service-level and policy information. Requirements depend on its category:

- Human-only services need a usable UI and human checkout or entitlement flow.
- Agent-only services need a machine-readable description, input/output schemas, a callable transport, and machine-compatible pricing and payment.
- Human-and-agent services need both complete surfaces, backed by the same service identity and consistent results and commercial terms where applicable.
- Paid delivery must be metered and recorded. Machine calls require idempotency and verifiable receipts; human transactions require an order record and appropriate receipt.
- Data services publish provenance and freshness appropriate to their claims.

For services with an agent interface, the canonical public manifest lives at:

```text
https://<service-host>/.well-known/agent-service.json
```

The same manifest can be indexed by the platform registry and exported to compatible discovery networks. Human-only services do not need an agent manifest.

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

For Niche Discovery, see the [continuous collection and agent-analysis design](docs/niche-discovery-architecture.md).
Its [Niche Manager Agent](docs/niche-manager-agent.md) owns autonomous research and knowledge-base maintenance through an extensible tool registry, memory, periodic runs and event-driven wakes.

## Design principles

- Protocol-first for agent-facing services: an agent can integrate from schemas without reading prose.
- Narrow services: each service owns a small domain and returns a useful result, not raw data alone.
- Payment-neutral core: commercial terms are stable while payment rails remain replaceable.
- Verifiable delivery: paid transactions have an order record; machine executions have a receipt tied to the request, price, and result.
- Safe autonomy for machine purchases: budgets, expiry, replay protection, and idempotency are enforced in deterministic code.
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

- `POST /web-evidence/v1/claims/verify/quick` — $0.02 quick verification.
- `POST /web-evidence/v1/claims/verify` — $0.05 standard verification.
- `POST /web-evidence/v1/claims/verify/deep` — $0.12 deep verification.
- `POST /web-evidence/v1/claims/verify/research` — $0.25 research-grade verification.

The earlier `/v1/services/web-evidence/claims/verify...` and `/v1/claims/verify...` paths remain supported as deprecated compatibility aliases.
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

For the production Docker Compose deployment at `api.aisoup.net`, follow [`deploy/google-cloud-vm.md`](deploy/google-cloud-vm.md). The public gateway accepts x402 and MPP payments in Base USDC at the listed tier prices. It also accepts card/USD payments through MPP Stripe at a $0.50 minimum per call. The Python service remains private behind an internal Bearer credential.

## Live discovery

California C-10 Contractor Check is live for humans through [Stripe Checkout](https://api.aisoup.net/contractor-check/) ($19/report) and for agents through its [paid HTTP API](https://api.aisoup.net/contractor-check/openapi.json) and [MCP endpoint](https://api.aisoup.net/contractor-check/.well-known/mcp/server.json) ($1/check in Base USDC). Its [Smithery listing](https://smithery.ai/servers/impanyu/contractor-check), [official MCP Registry record](https://registry.modelcontextprotocol.io/v0/servers/io.github.impanyu%2Fcontractor-check/versions/0.1.0), and route on the [shared x402Scan page](https://www.x402scan.com/server/131c08a0-027b-4f28-afe6-2a72fdbac7dc) are publicly visible. The release contract and repeatable validation gates are in [`docs/agent-service-release.md`](docs/agent-service-release.md). Other external directory entries and a settled payment are not yet verified.

Web Evidence is published through the following public discovery surfaces:

- [Smithery](https://smithery.ai/servers/impanyu/web-evidence) as the hosted MCP listing for `impanyu/web-evidence`.
- [Official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers/io.github.impanyu%2Fweb-evidence/versions/0.3.1) as `io.github.impanyu/web-evidence`.
- [OpenX402 Bazaar](https://facilitator.openx402.ai/discovery/resources?type=mcp) as the paid MCP resource `mcp://api.aisoup.net/verify_claim_quick`.
- [x402Scan](https://www.x402scan.com/server/131c08a0-027b-4f28-afe6-2a72fdbac7dc) with all four paid HTTP tiers and both public snapshot routes.
- [MPPScan](https://www.mppscan.com/server/b915b7bda6517cd0e47f1cfaca657b4a4f7b268093117889d34b31bad1915664) with the paid verification routes and public evidence/commerce routes it can safely crawl.
- [Global A2A Registry](https://www.a2a-registry.org/agent/net.aisoup.web_evidence) and the [open-source A2A Registry API](https://a2aregistry.org/api/agents?search=Web%20Evidence) via the public A2A 1.0 Agent Card.

The repository also carries `server.json` for MCP Registry publication and `glama.json` for a future Glama submission. The landing page publishes Schema.org service metadata, `robots.txt`, `sitemap.xml`, OpenAPI, `llms.txt`, and well-known manifests for independent crawlers. A directory is only treated as live after its public listing can be retrieved independently.

The `api.aisoup.net` URL-prefix property is verified in Google Search Console and its sitemap has been submitted. Production also exposes a private-key-backed IndexNow ownership file so updated discovery URLs can be sent to participating search engines. Search-engine inclusion remains asynchronous and is not treated as complete until the result is publicly searchable.

## Status

The repository contains the v0 protocol, a runnable tiered Web Evidence service, full provider-source provenance, URL snapshots with raw and normalized SHA-256 hashes, SQLite persistence, machine-readable discovery, and an x402/MPP dual-protocol payment gateway. Each paid HTTP, MCP, or A2A execution creates an order, signed receipt, and detailed revenue/cost ledger entry. The admin dashboard reports per-order OpenAI token and Web Search costs, gross profit, and margins. Production USDC settlement and public MCP, A2A, x402, and MPP directory discovery have been verified. MPP Stripe has passed an isolated two-account sandbox payment; a real live-mode card charge remains an acceptance requirement. The next milestone is additional evidence operations and ongoing directory health monitoring.

Niche Manager runtime and deployment: [operations](docs/niche-manager-operations.md).


Photo Scout's agent API is public at `https://api.aisoup.net/photo-scout/mcp`.
The free `list_photo_scout_prices` tool describes pricing and coverage; the paid
`discover_photo_spots` tool costs $2 in Base USDC using x402 MCP. The HTTP endpoint
retains MPP and the human website remains a free preview. Its [official MCP Registry
record](https://registry.modelcontextprotocol.io/v0/servers/io.github.impanyu%2Fphoto-scout/versions/0.1.0)
was verified publicly. Smithery publication awaits account authorization; a settled
payment has not been independently verified. See [release metadata](services/photo-scout/release.json)
and [API documentation](docs/photo-scout.md).
