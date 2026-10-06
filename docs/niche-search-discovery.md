# Search discovery layer

Search discovery identifies possible source links across social discussion, media,
shopping/product reviews and professional workflows. It does not ingest webpage
content, and search results never enter the signal/evaluation tables automatically.

The initial adapter uses Brave's official web-search endpoint. Eight configurable-in-code
query templates include Reddit alternatives/wishes, shortages, product-review gaps,
manual workarounds, willingness to pay, and two Chinese-language queries. Results
are restricted to the provider's recent-month filter and top ten per query. This
is sampled search-index coverage, not continuous Reddit coverage or whole-web coverage.
No publication date, author identity, complaint frequency or market demand is
inferred from the search response. English and Chinese queries do not guarantee
coverage of either language or any particular country.

## Setup

The default is **disabled**. No search API credential was found in the local or
production environment during setup on 2026-10-05; no live provider requests or
purchases were made. Before enablement, configure server-only:

- `BRAVE_SEARCH_API_KEY`: provisioned API key, never committed or sent in chat.
- `NICHE_SEARCH_STORAGE_REFERENCE`: reference to an actual plan/agreement allowing
  persistent storage of search results. An operator attestation is not automatic
  license verification. Even storing only URLs requires reviewing the provider's terms.
- `NICHE_SEARCH_DAILY_REQUEST_LIMIT=8`: hard cap per UTC day, allowed range 1–32.
  Failed requests consume quota too. With eight queries, the default permits one
  full cycle/day; the existing six-hour scheduler can attempt collection but stops
  when the daily budget is exhausted. This cap is not an account-wide billing cap
  and does not cover requests from other applications.
- `NICHE_COLLECT_SEARCH=1`: only after the preceding requirements are satisfied.

The currently advertised Search price is $5/1,000 requests, with $5 monthly credit;
these do not establish storage rights or authorize a purchase. At eight requests/day,
31 days would produce at most 248 attempted requests from this service. Actual
billing and eligible storage plans must be confirmed in the account before enablement.

[Official API, pricing and storage/copyright FAQ](https://brave.com/search/api/)

## Internal operation

- `POST /niche-discovery/v1/admin/collect/search`: run configured discovery.
- `GET /niche-discovery/v1/admin/search-leads`: read up to 100 private leads.
- `GET /niche-discovery/v1/admin/collection-status`: inspect latest collection state.

All three require the existing `X-Admin-Key` and are excluded from public OpenAPI.
Leads have a stable normalized URL hash, domain, provider, first/last discovery time
and `discovery_only` state. Query matches are recorded separately. Tracking query
parameters and URL fragments are removed; no titles, snippets or raw responses are
stored. Deduplication does not imply distinct human complaints. Leads expire after
30 days without rediscovery, including during a manual disabled run.

No destination URL is fetched, including Reddit. Authentication, throttling or
transport errors stop the run without retry loops; errors contain no key or raw
provider response. Daily request reservations are atomic and shared by scheduled
and manual runs. Changing query templates changes discovery scope, not source rights.

## Evidence workflow

A reviewer uses leads to identify themes and locate independent permitted evidence
through existing source adapters. A lead ID cannot be linked into a niche draft,
counted toward the publication floor, shown as evidence, or used for scoring/trend
metrics. A provider storage grant does not grant rights to third-party page content.
Search lead topics are unverified hints; any subsequent source collection, retention
and commercial analysis must use its own permitted access path. There is no
public contribution endpoint and no automatic promotion bypass.

Before declaring this source Running, verify real provider results, repeated-run
deduplication, quota behavior and actual configured rights. Mocked tests establish
implementation behavior only.
