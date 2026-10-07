# Niche Discovery signal-source register

Owner: Dream Workshop LLC. Updated: 2026-10-05. This is the living queue for adding **continuous** signal collection. A source is marked **running** only after production configuration, a successful scheduled run, stored source-linked candidates, and a repeat-run check. Public visibility alone is not permission to ingest or monetize data.

## Status and acceptance rules

- **Candidate:** useful signal, access and rights still to be investigated.
- **Ready to build:** official feed/API and commercial-use path reviewed; bounded collection plan defined.
- **Implemented:** adapter exists and tests pass, but production collection is not confirmed.
- **Running:** enabled in production, fresh records observed, failures monitored.
- **Permission needed:** separate agreement, owner authorization, or API credential is required.
- **Deferred:** low signal-to-noise, unreliable access, or disproportionate rights/maintenance cost.

Every adapter must record source URL, platform, source identifier, observation time, collection time, query/scope, and review state; deduplicate records; bound rate and retention; handle removal requests; and keep copied text out of public reports. Media coverage, social discussion, and reviews are *candidate signals*, not proof of market size or willingness to pay. Source counts must be normalized for syndication and correlated posts before scoring.

## Internal aggregate product telemetry (future)

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P0 | Our searches with no results, repeated query refinements | Unmet information need | First-party analytics with privacy notice; no raw identifiers | Candidate | Define aggregate event schema and opt-out/retention |
| P1 | Our paid API failures, support tickets, cancellations | Pain plus payment or churn evidence | First-party; minimize customer data | Candidate | Add service-specific anonymized reason codes |

## Search-index discovery (leads only)

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P0 | [Brave web search](https://brave.com/search/api/) | Candidate links across Reddit, media, shopping and professional workflows | Official search API plus plan granting persistent search-result storage; no downstream webpage rights implied | **Implemented; disabled**, no credential or live run | Configure provider key and storage grant; see [search discovery setup](niche-search-discovery.md); verify actual results before marking Running |

Search leads are kept outside the evidence tables. No Reddit pages are fetched;
unverified search results are not paid-report evidence or inputs to niche scores.

## Public discussion and developer communities

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P0 | [Hacker News Ask HN](https://github.com/HackerNews/API) | Explicit questions about tools, alternatives, workarounds | Official public API; sample recent questions, link back | **Running**; six-hour VM job added 4 pending signals, repeat run added 0 duplicates on 2026-10-05 | Review useful-candidate rate and false positives after several cycles |
| P0 | [GitHub Issues](https://docs.github.com/en/rest/issues/issues) | Feature requests, recurring bugs, missing integrations | Official API; repository allowlist and rate limits | **Running**; WooCommerce and Home Assistant pilots added 22 pending signals, repeat run added 0 duplicates on 2026-10-05 | Review candidate quality before widening the repository list |
| P0 | [Reddit](https://redditinc.com/policies/data-api-terms) | Domain-specific pain, substitutions, willingness to pay | **Permission needed:** commercial API use requires a separate Reddit agreement | Implemented; permission needed; disabled | OAuth adapter and mocked tests complete; see [Reddit access plan](reddit-access-request.md). Obtain commercial agreement and approved application before collection |
| P1 | [Stack Exchange network](https://api.stackexchange.com/) | Repeated expert questions across many industries | Official API; attribution and CC BY-SA obligations require design review | Candidate | Select sites, review attribution/retention, ingest links and metadata |
| P1 | [Bluesky](https://bsky.network/docs/category/http-reference/) | Emerging complaints and “wish there were” posts | Public AppView API, visibility preferences; current anonymous search returned 403 in our environment | Candidate | Verify reliable access and respect visibility/deletion labels |
| P1 | Mastodon public timelines | Community-specific pain | Server-specific API, policies and opt-out expectations | Candidate | Pilot opt-in servers with published API rules |
| P2 | Discord, Slack, private forums, Facebook Groups | Detailed practitioner workarounds | **Permission needed:** workspace/community owner authorization and member notice | Permission needed | Recruit opt-in communities; do not scrape private spaces |
| P2 | X, TikTok, Instagram, Quora | High-volume discussion and comments | Platform-specific paid APIs/terms, quotas, deletion requirements | Candidate | Price and rights review before implementation |
| P2 | [YouTube comments](https://developers.google.com/youtube/terms/derived-metrics-policy) | Repeated requests in tutorials and product reviews | Official API; refresh and deletion limits | Candidate | Review allowed retention and quota for commercial analysis |

## Media, institutions, and public records

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P0 | [GDELT news index](https://gdeltproject.org/about.html) | News reports about unmet needs, regulation, shortages, emerging workarounds | Open commercial-use GDELT data with attribution; linked publishers retain rights to articles | **Implemented**, not enabled; local and VM probes returned 429 | Resolve API access/rate limits, then configure narrow queries and verify recurring records |
| P1 | Trade publications, newsletters, podcasts | Specialist pain and new category formation | Publisher RSS/API or licensed feed; no full-text republication | Candidate | Source-by-source feed and terms review |
| P1 | [CFPB complaint database](https://www.consumerfinance.gov/data-research/consumer-complaints/) | Structured financial-product complaints | Public API; narratives changed in 2026 | Candidate | Build structured issue/volume adapter without assuming new narratives |
| P1 | Municipal 311 open-data portals | Local service failures and repeated requests | City open-data APIs and licenses vary | Candidate | Pilot two cities with stable APIs and geographic normalization |
| P1 | Public procurement/RFP portals, e.g. SAM.gov | Buyer with explicit project scope and potential budget | Official API/registration terms | Candidate | Review API key and reuse rules; capture category, amount, deadline |
| P1 | Public regulatory comments, recalls, safety reports | New compliance burdens and costly failure modes | Agency-specific open APIs; high-stakes interpretation | Candidate | Pilot one agency; label as context, not product demand by itself |
| P2 | Patent filings, standards discussions, grant awards | Supply, technical shifts, funded priorities | Public registries; weak direct demand signal | Candidate | Use only as corroboration, not primary demand evidence |

## Shopping, app marketplaces, and commercial intent

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P0 | Merchant-provided returns, support reasons, product Q&A and review exports | Verified purchase pain, refunds, alternatives | **Permission needed:** merchant opt-in and platform-specific data rights | Permission needed | Offer merchant import/connector with limited scope and deletion contract |
| P0 | Amazon, eBay, Etsy, Walmart product reviews and Q&A | Repeated product gaps and buyer language | Marketplace-specific API/partner terms; public pages do not grant bulk commercial reuse | Permission needed | Negotiate or use approved partner APIs; no unauthenticated scraping |
| P1 | Apple App Store and Google Play reviews | Product gaps, price objections, feature requests | Official owner APIs generally scoped to own apps; third-party rights to be checked | Candidate | Pilot developer-authorized app portfolios |
| P1 | Shopify and other commerce app marketplaces | Merchant operational pain and integration gaps | Review API and marketplace terms individually | Candidate | Start with authorized app developers/merchants |
| P1 | [Steam user reviews](https://partner.steamgames.com/doc/webapi/IUserReviewsService) | Purchased-product complaints in a digital marketplace | Public API exists; [Steam API terms](https://steamcommunity.com/dev/apiterms) limit permitted distribution/use | Permission needed | Obtain rights review or Valve approval before using in paid analysis |
| P1 | Product returns, chargebacks, help-desk taxonomies | Costly unresolved pain | First-party/merchant permission; sensitive data minimization | Permission needed | Build opt-in aggregate ingestion, never raw customer identities |
| P1 | Search ads keyword and shopping-query data | High-intent search and CPC as spending proxy | Paid/authorized platform APIs; query volume is not purchases | Candidate | Review Google Ads/Microsoft Ads access and budgets |
| P2 | Crowdfunding comments, wishlists, preorders | Early purchase intent and missing product variants | Platform API/rights vary; public totals can be misleading | Candidate | Source-by-source review; treat pledges separately from comments |

## Work and spend signals

| Priority | Source | Signal | Access / rights | Status | Next action |
|---|---|---|---|---|---|
| P1 | Job postings and contractor/freelance briefs | Organizations paying people to work around a problem | Job-board API/terms vary | Candidate | Track repeated roles, task wording, salary/budget, employer duplicates |
| P1 | B2B software requests and implementation tenders | Explicit budget, integration pain, purchasing process | Vendor/community authorization or public procurement API | Candidate | Build vertical pilot around one open tender source |
| P2 | Paid templates, courses, communities and plugins | Evidence of spending on a workaround | Sales counts often private or estimated | Candidate | Validate with seller-provided figures or direct interviews |

## Collection order

1. Stabilize public Hacker News, GitHub and GDELT adapters; measure useful-candidate rate, dedupe, freshness, and review workload. GDELT returned HTTP 429 during a local probe, so backoff and production verification are required.
2. Add representative GitHub repositories, CFPB structured complaints, and one open procurement/311 source. These diversify domains without depending on restricted social APIs.
3. Pursue Reddit commercial access and opt-in merchant data in parallel. These are likely high-value sources but require authorization before production ingestion.
4. Add marketplace/app-review and other social adapters one at a time after rights, access, deletion, and attribution checks. Keep a provider-level kill switch and source health log.

Do not label the service “whole-web coverage.” Report actual covered platforms, regions, languages, collection windows, and known gaps on each niche evaluation. Cross-source corroboration matters more than a large unfiltered post count.

## October 6 expansion (implementation; production verification pending)

| Source | Implementation and scope | Current verification |
|---|---|---|
| Stack Exchange | Official API; configured DIY/gardening/money/travel/bicycles/cooking sites; current CC BY-SA 4.0 headlines, author/profile/license attribution | Local live collection succeeded; bounded keyword research and repeat dedupe tests pass |
| CPSC | Recent official recall titles, URLs and dates; contextual product-safety signal | Local live collection succeeded; does not count toward demand publication threshold |
| Federal Register | Recent official titles/URLs/dates, optional agent keyword query | Local live collection succeeded; regulatory context only |
| Global Voices | CC BY 3.0 RSS headlines, dates/bylines/source attribution; operator-approved advertised WebSub hub | Local live headline collection succeeded; actual hub verification still pending |
| NYC 311 | Non-identifying administrative metadata adapter; extra rights-reference gate | Public API probe succeeded; disabled pending specific reuse review |
| CFPB | Structured complaint-field adapter; no narratives | Public API probes timed out; disabled; field mapping still needs a successful live response |

The common adapter framework enforces bounded responses, provider attempt budgets,
backoff, short query-cache windows, source identifiers and private excerpts. WebSub
adds registration, verified leases and renewal to existing signed callback/polling
support. [On-demand research](niche-on-demand.md) uses the same autonomous manager;
private search objectives are not contributed demand signals. This expansion still
does not claim all-platform coverage; commercial shopping/review/social access remains
tracked above and must be connected individually.
