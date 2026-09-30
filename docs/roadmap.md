# Roadmap

## Milestone 0 — protocol baseline

- Define the canonical service manifest and example.
- Fix the platform vocabulary: service, operation, offer, quote, invocation, settlement, receipt.
- Document discovery, payment, execution, and trust boundaries.

Exit condition: an example manifest validates against the schema and describes a complete purchasable operation.

## Milestone 1 — end-to-end reference path

- Build a registry API that ingests and searches manifests.
- Build a gateway with quote, idempotency, metering, and receipt endpoints.
- Add one x402-compatible pay-per-call adapter and a local test adapter.
- Add TypeScript and Python buyer SDKs.
- Launch one narrow information service with measurable value and freshness.

Exit condition: a fresh external agent can discover, pay for, call, and verify the reference service without manual account setup.

## Milestone 2 — recurring commercial relationships

- Add prepaid balances and usage ledgers.
- Add metered subscriptions, limits, and renewal state.
- Add signed spending mandates and organizational budget policies.
- Add provider payouts, refunds, tax records, and reconciliation.

Exit condition: an agent can repeatedly consume services under a bounded entitlement without paying on-chain for every invocation.

## Milestone 3 — service matrix

- Add provider onboarding and endpoint ownership verification.
- Add health, latency, freshness, and delivery-quality observations.
- Add capability-based ranking and policy-aware selection.
- Add MCP and A2A adapters and external discovery exporters.
- Add composition receipts for workflows that purchase from multiple services.

Exit condition: agents can choose among substitutable services and compose multiple paid results with an auditable cost chain.

## Choosing the first service

The first production service should have:

- a narrow decision or information need;
- inputs and outputs that can be validated automatically;
- data whose freshness or synthesis is worth paying for;
- a low marginal cost and predictable latency;
- clear provenance and redistribution rights;
- a buyer population that already operates through APIs or agents.

Avoid beginning with a broad assistant. A small service with a crisp contract is easier to price, evaluate, discover, and compose.
