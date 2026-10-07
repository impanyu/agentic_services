# Niche Discovery database search and background query inspirations

Current product policy (2026-10-06): **database-only lookup; no on-request generation**.
A miss returns an empty result, not a queued exploration. Human searches are free
three times per UTC day; the human subscription remains $9.99/month and is not
subject to the three-search limit. Agents use separately metered per-call search.
Subscriptions remain unavailable until published reports and actual Stripe delivery
are verified; no test subscriber is fabricated in production.

## Human access

`GET /niche-discovery/v1/search?q=…` supports q (3..150 characters), optional category,
sort=score/recent and limit=1..20. Search results include complete published evaluations
and quota metadata. The English UI uses this endpoint; three free lookups, including
misses, count each UTC day. A subscriber `Bearer nd_…` token removes the free limit.
`GET /niches` remains a free preview catalog; nonempty q directs clients to the search
endpoint. Private drafts are never returned by search or preview.

Anonymous quotas use a stable HMAC of the network address supplied by the trusted
local reverse proxy; the gateway overwrites client-supplied visitor/gateway assertions.
Raw addresses are not stored in the query database. Shared networks may share the
allowance; this is a pilot anonymous-client quota, not verified personal identity.
Subscriber use is grouped by hashed Stripe customer ID across token rotations.
Search responses use no-store. Browser tokens remain in tab memory only.

## Agent access

`GET /niche-discovery/v1/search/pay-per-call?q=…` returns an existing Base USDC/MPP
payment challenge. Trial price is configurable `NICHE_SEARCH_PRICE_USD=0.05` per
search, up to 20 evaluations; a zero-match search in a nonempty catalog is still a
search. Invalid criteria are rejected before payment. If the entire published catalog
is empty, paid search returns 503 before any charge. Existing per-evaluation
`GET /niches/{id}/pay-per-call` remains $0.25. Agent monthly subscriptions are no
longer advertised. Human accounts retain their existing browser subscription access.
Human/agent channels are explicit API products, not inferred from User-Agent strings.

The backend serves paid search only after the private gateway authenticates it;
caller-supplied payment/gateway headers cannot bypass the public payment middleware.
A live 402/503 check is not proof of settled payment; real paid delivery must be
verified before claiming a paid launch.

## Query inspirations

Search keywords, category, result count, channel, timestamp and hashed quota identity
are retained for up to 30 days. Email addresses, recognizable access keys and long
phone-like values are removed from stored keywords. No raw IPs, emails or access
credentials are logged in this table. Query text remains untrusted private data;
please do not enter personal or confidential information.

The manager has `inspect_query_inspirations` and sees five aggregate topics in its
wake context. It may choose related research on a later scheduled/source-triggered
wake. Topics are ranked by missing results and search frequency, with distinct-client
counts treated as approximations. Query interests do **not** create source evidence,
wake the agent immediately, validate demand, or prove willingness to pay. No query
contribution API or public raw-query feed exists. Empty-result searches do not create
research events. Previously queued on-demand verification jobs are cancelled.

## Broader sources and real subscriptions

The source registry and adapters are unchanged by the database-only policy:
Stack Exchange (DIY, gardening, money, travel, bicycles, cooking), official CPSC recalls,
Federal Register notices, approved RSS/Atom headlines, and gated NYC 311/CFPB adapters,
in addition to existing HN/GitHub/GDELT/permitted Reddit/search connectors.
`research_source` lets the autonomous agent choose keywords during background work.
Source permission, enabled configuration, observed collection and market validation
remain distinct. API/feed bodies are bounded, HTTPS public-address checked, no
redirects followed, with provider daily limits, short duplicate-request suppression,
backoff, source identifiers and attribution/license metadata. Headlines only are
retained from RSS/Atom; full articles and media are not copied. Context notices do
not fill the demand publication threshold. Stack Exchange sites count as one source
group for publication, not as independent publisher domains.

WebSub uses only the operator-approved advertised hub/topic and server-side callback
secret. The worker registers, validates callback challenges, accepts signed pushes,
queues source references, renews before lease expiry and unsubscribes disabled feeds.
Hub 202 means pending; only verified intent establishes an active lease. Push payloads
are wake-up hints, not model instructions or evidence. Polling continues as fallback.
Public `/sources` shows configuration, sanitized collection results and lease state;
it does not expose query topics, callback capabilities or secrets.

References: [W3C WebSub](https://www.w3.org/TR/websub/),
[Global Voices attribution/license](https://globalvoices.org/about/global-voices-attribution-policy/),
[Stack Exchange API terms](https://stackoverflow.com/legal/api-terms-of-use).
