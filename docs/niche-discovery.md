# Niche Discovery — first release

Niche Discovery is a cross-industry service for people and agents. Human page:
`https://aisoup.net/niche-discovery/`. Machine API:
`https://api.aisoup.net/niche-discovery/v1/`. Anyone can submit a complaint,
suggestion, or proposal. Public listing and keyword search are free. A USD
9.99/month Stripe subscription unlocks complete evaluations for both people
and agents via the same `nd_` bearer token. Agents may instead buy one complete
evaluation per call for USD 0.25 through the existing Base USDC payment gateway
at `GET /niche-discovery/v1/niches/{id}/pay-per-call`.

The first release is intentionally evidence-first. Submissions remain private
pending editorial review. An editor can link signals into a niche. A niche
cannot be published without at least three linked signals from two source
domains. A signal with a user-supplied URL is **not** independently verified by
the server; an editor must check it before publication. Published reports show
links and metadata, not the underlying copied complaint text. Direct
submissions are limited to five per IP hash per hour, and the hash is not
returned to API clients.

The 0–100 score is an editorial hypothesis: pain 25%, frequency 20%,
willingness to pay 20%, reachable buyers 15%, feasibility 10%, and competition
gap 10%. It is not a measured TAM, revenue forecast, or customer count. The
API returns each dimension, source count, source-domain count, two 30-day
windows, and evidence links. Trend is shown only after ten observations in
those windows. Forecast remains unavailable until longitudinal observations
and out-of-sample calibration exist. That missing data must not be silently
filled with model guesses.

Automated source adapters queue candidates for editorial review; none
auto-publish a niche. GitHub Issues collection uses explicitly configured
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
