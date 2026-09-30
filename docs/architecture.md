# Architecture

## Goal

An internet-connected agent should be able to complete this loop without a human creating an account or copying an API key:

```text
discover -> inspect -> quote -> authorize -> pay -> execute -> verify -> reuse
```

Human policy still controls the agent's wallet, spending limits, approved categories, and escalation rules.

## Platform model

### Service plane

A service is a small, independently deployable information product. It may wrap proprietary analysis, licensed data, public data, a model, a database, or another API. It exposes one or more operations with explicit JSON input and output schemas.

The service owns domain logic. It does not implement wallets or settlement directly.

### Gateway plane

The gateway is the commercial boundary in front of services. It:

- resolves the selected offer;
- returns a quote or payment challenge;
- verifies payment or subscription entitlement;
- enforces idempotency, budgets, rate limits, and expiry;
- invokes the service;
- records metering and produces a signed receipt.

Service code receives a normalized invocation context, independent of the payment rail.

### Registry plane

The registry indexes signed service manifests. Search works on capabilities and constraints rather than service names alone. Ranking can use price, freshness, latency, availability, provenance, and buyer policy.

Registry ingestion must verify manifest syntax, ownership of the advertised endpoint, and health. Runtime quality signals remain distinct from provider claims.

### Settlement plane

Payment adapters implement a shared interface:

```text
createChallenge(offer, request) -> challenge
verifyAuthorization(challenge, proof) -> authorization
capture(authorization, delivery) -> settlement
refund(settlement, reason) -> refund
```

The first implementation should target per-call machine payments. Credits and subscriptions use the same authorization result but settle against an internal entitlement ledger. Additional adapters can support mandate-based or conventional payment rails.

## Discovery

Each service publishes a canonical manifest at `/.well-known/agent-service.json`. The manifest describes:

- stable service identity and provider;
- natural-language and structured capabilities;
- HTTP, MCP, and A2A endpoints;
- operations and their input/output schemas;
- commercial offers and accepted payment methods;
- data provenance, freshness, geographic coverage, and policies;
- health, support, and service-level targets.

The platform registry stores a normalized projection of the manifest. Exporters may translate compatible offers into other discovery formats, including x402 Bazaar resources. The canonical manifest stays payment-rail neutral.

### Multi-service commerce identity

Every quote, order, receipt, ledger projection, and admin aggregate carries a stable `serviceId`. The first service uses `web-evidence`; future services register a new identifier instead of introducing service-specific commerce tables or response shapes.

The public `GET /v1/services` endpoint exposes the platform catalog. Admin summary and order APIs accept an optional `serviceId` query parameter. Omitting it returns platform-wide totals, while `byService` preserves the per-service breakdown. Pagination uses `limit`, `offset`, and `total`, so the dashboard and external admin clients share the same contract.

## Invocation flow

1. The buyer searches the registry or resolves the well-known manifest.
2. The buyer selects an operation and offer whose constraints match its task and policy.
3. The gateway creates a quote containing a unique ID, exact amount, currency or asset, expiry, request hash, and accepted payment methods.
4. The buyer supplies a payment proof, subscription entitlement, or prepaid-credit authorization.
5. The gateway verifies the proof and reserves or settles funds.
6. The gateway invokes the service using the same request hash and idempotency key.
7. The service returns a result plus provenance and freshness metadata.
8. The gateway returns the result with a signed receipt tying together the quote, payment, request, and response hashes.
9. Retries with the same idempotency key return the same recorded outcome and do not charge twice.

## Trust boundaries

Agent identity, payer identity, and end-user identity are separate principals. A request may contain all three, but the service only receives claims required for its operation.

The gateway must enforce these invariants in deterministic code:

- quotes expire and cannot be replayed for a different request;
- authorization amount, asset, recipient, operation, and request hash match the quote;
- autonomous purchases stay within signed mandates and platform budget policy;
- a successful payment cannot result in more than one charge;
- secrets and raw payment credentials never enter prompts or service payloads;
- result provenance and observed freshness are stored with the receipt;
- refunds and failed deliveries have explicit states.

## Core records

The control plane will eventually persist the following records:

| Record | Purpose |
| --- | --- |
| `Provider` | Legal/technical owner, signing keys, settlement accounts |
| `Service` | Stable identity and current manifest version |
| `Operation` | Callable capability with typed input and output |
| `Offer` | Pricing model, amount, limits, and payment methods |
| `Quote` | Time-bound commercial commitment for one request |
| `Authorization` | Verified right to spend or consume entitlement |
| `Invocation` | Idempotent execution state and request/response hashes |
| `Settlement` | Captured payment, refund, or ledger movement |
| `Receipt` | Signed evidence joining commercial and delivery records |

## Protocol compatibility

- **HTTP/OpenAPI** is the baseline transport for simple services.
- **MCP** exposes tools and resources to model clients; OAuth-based authorization remains separate from payment authorization.
- **A2A** is used when the service itself is a long-running or conversational agent.
- **x402** is a strong first adapter for low-friction pay-per-call HTTP and MCP services.
- **AP2-style mandates** fit higher-value or delegated purchases where proof of user intent and dispute evidence matter.

These protocols solve different parts of the stack and should be composed rather than treated as mutually exclusive platform choices.
