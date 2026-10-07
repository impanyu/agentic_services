# Niche Discovery — first release

See the [continuous collection and agent-analysis architecture](niche-discovery-architecture.md)
for the target information model and implementation sequence, and the
[Niche Manager Agent design](niche-manager-agent.md) for autonomous tools, memory,
periodic execution and external-event wakes. The sections
below describe the current first release.

Niche Discovery is a cross-industry service for people and agents. Human page:
`https://aisoup.net/niche-discovery/`. Machine API:
`https://api.aisoup.net/niche-discovery/v1/`. Signals are collected from configured external sources; users and agents consume
reviewed market information through the UI and API. The preview catalog is free.
Human visitors get three database searches per UTC day; the USD 9.99/month human
subscription removes that free-search limit. Agent database search is USD 0.05/call
(configurable), up to 20 full published evaluations, through the Base USDC/MPP gateway
at `GET /niche-discovery/v1/search/pay-per-call`. The existing USD 0.25 per-evaluation
endpoint remains available. No lookup starts live generation; private query interests
can inspire later background research. See [query policy](niche-query-service.md).


The first release is evidence-first and one-way: source collection → private
review → niche evaluation → human UI and agent API. There is no public signal
contribution endpoint or form. The autonomous manager can link collected signals into a niche, revise assessments,
merge/split records and publish supported evaluations. An admin editor remains available.
Publication requires at least three signals from two source domains. Editors
check the original evidence; reports show links and metadata rather than copied
source excerpts.

The 0–100 score is an editorial hypothesis: pain 25%, frequency 20%,
willingness to pay 20%, reachable buyers 15%, feasibility 10%, and competition
gap 10%. It is not a measured TAM, revenue forecast, or customer count. The
API returns each dimension, source count, source-domain count, two 30-day
windows, and evidence links. Trend is shown only after ten observations in
those windows. Forecast remains unavailable until longitudinal observations
and out-of-sample calibration exist. That missing data must not be silently
filled with model guesses.

Automated source adapters queue private candidates and wake the manager when new
signals arrive. Adapters do not publish; the manager selects research tools and
commits assessments through deterministic evidence and revision checks.
See [manager operations](niche-manager-operations.md). GitHub Issues collection uses explicitly configured
repositories, fetches at most 50 recent open issues per repository, skips pull
requests, deduplicates by GitHub issue ID, and removes email addresses.
Configure `NICHE_GITHUB_REPOSITORIES` with reviewed `owner/repo` pairs.
Before adding a repository, review [GitHub's API terms](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)
and [rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
The Hacker News adapter samples at most 30 recent Ask HN questions through
its [official API](https://github.com/HackerNews/API), selecting explicit
requests for tools, alternatives, or help. Set `NICHE_COLLECT_HACKER_NEWS=1`
to run it every six hours. The GDELT adapter queries its
[public news index](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
with `NICHE_GDELT_QUERY`, saving linked headlines rather than articles.
[GDELT attribution](https://gdeltproject.org/about.html) is required in
analysis and reports. HTTP 429 is treated as a failed collection cycle, not
silently retried in a burst.

The [source register](niche-signal-sources.md) tracks media, social,
marketplace, first-party, procurement, and other signal sources one by one.
In particular, [Reddit's commercial Data API](https://redditinc.com/policies/data-api-terms)
requires a separate agreement; [YouTube comment storage](https://developers.google.com/youtube/terms/derived-metrics-policy)
has refresh/deletion limits. The service must not quietly scrape either
platform or republish user comments.

## Billing and operations

Stripe product: `prod_VO5D10PzgtREe0` on the Agentic Services account;
monthly price: `price_1UNJ3LAousqZskravoCVP7AJ` (USD 9.99). The product
exists in Stripe, but that fact alone does not mean Checkout or delivery is
live. Checkout remains disabled until at least one reviewed niche is published,
the webhook and SMTP are configured, and the price/key are valid. Set
`NICHE_STRIPE_PRICE_ID` and a restricted `NICHE_STRIPE_SECRET_KEY`
with the permissions listed in `.env.example`. Checkout verifies that the
configured Stripe price is active, USD 9.99, and monthly before redirecting.
Set an independent webhook destination at
`https://api.aisoup.net/niche-discovery/v1/stripe/webhook` for
`checkout.session.completed`, `checkout.session.async_payment_succeeded`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`invoice.paid`, and `invoice.payment_failed`. Store its signing secret as
`NICHE_STRIPE_WEBHOOK_SECRET`. A token derived from `NICHE_TOKEN_SECRET` is
emailed after a verified paid Checkout session and matching active
subscription. Duplicate delivery does not send a second email. Subscription
updates deactivate tokens when Stripe reports a non-active status. The
Billing Portal route uses the token to open a customer-specific portal session.

Before accepting live customers, verify SMTP delivery, the Stripe portal's
cancellation configuration, webhook ingress, duplicate event handling, a
real Checkout return, renewal/cancellation behavior, and that the first
published niche contains credible evidence. Run a live test transaction and
confirm that the buyer receives the token even if they never return to our
site. A preview page with an empty catalog is not a paid launch.

Because the VM has a separately preserved Kalshi service in its live Caddy
configuration, merge the Niche Discovery Caddy blocks into `/etc/caddy/Caddyfile`
instead of replacing the whole file from this repository.

### Reddit adapter

Reddit posts and comments are implemented through an approval-gated OAuth adapter,
with private candidate review, bounded community sampling, deduplication, retained
content refresh and deletion of dependent reports when evidence changes or disappears.
Collection is disabled pending explicit commercial approval and an approved API app.
See [Reddit access request and setup](reddit-access-request.md) for the draft request,
proposed communities, exact coverage limits and configuration. Tests use synthetic
fixtures; no Reddit commercial access or live collection is claimed.

### Whole-web search discovery

The [search discovery layer](niche-search-discovery.md) collects candidate URLs
through an approved search API, separately from source evidence. It can locate
Reddit discussions and other media/shopping/professional pages without requesting
those pages. Leads remain internal, do not count toward scores or publication,
and require independent permitted source evidence before use in evaluations.
The adapter is implemented but disabled pending a key and search-result storage
rights. No new paid service has been purchased.
