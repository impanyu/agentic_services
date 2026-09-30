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
- `GET /openapi.json` — inspect the complete HTTP contract.
- `GET /llms.txt` — read concise agent integration instructions.

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

## Status

The repository contains the v0 protocol, a runnable tiered Web Evidence service, full provider-source provenance, URL snapshots with raw and normalized SHA-256 hashes, SQLite persistence, machine-readable discovery, and an x402/MPP dual-protocol payment gateway. Production payment settlement has been verified; the next milestone is broader external catalog indexing and additional evidence operations.
