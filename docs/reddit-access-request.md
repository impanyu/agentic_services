# Reddit access plan — Niche Discovery

Updated 2026-10-05. **Draft only; not submitted. No live Reddit data collected.**

## Proposed commercial request

We operate Niche Discovery at https://aisoup.net/niche-discovery/, with a human interface and authenticated agent API. Subscribers pay $9.99/month; agents can also pay $0.25 per evaluation. We want explicit written permission for commercial analysis of public posts and comments to discover recurring problems, product gaps and requests for alternatives, and to provide evidence-linked, aggregated market hypotheses to subscribers.

Please confirm permitted communities, request volume, retention, attribution, deletion requirements, derived report distribution, and whether automated analysis or inference through third-party model providers is allowed. We will not train models on Reddit data. AI processing is not part of the initial adapter and requires separate agreement review before integration.

The pilot proposes up to 20 approved communities, once every six hours. Each run reads at most two pages of 50 recent posts and two pages of 50 recent comments per community, plus refreshes retained evidence through `/api/info` in batches of 100. This is a sampled window, not complete coverage. For 16 communities, new-content reads are at most 256 API requests/day, with additional validation calls proportional to retained evidence. Quotas and frequency must be adjusted to the actual agreement; the current six-hour schedule is not a promise to Reddit.

We retain only a private excerpt of at most 500 characters, content ID, community, permalink and observation time. We discard author identity, email addresses, user mentions and embedded URLs; this does not guarantee removal of every personal detail, so candidates remain private for human review. We skip adult-marked and removed/deleted content and do not infer sensitive characteristics about individual users. No voting, messaging, posting or private-community access is requested.

The proposed retention ceiling is 30 days, shorter if the agreement requires. We refresh retained items each run; missing, deleted, removed or edited evidence is purged or replaced. Dependent draft/published reports are deleted so conclusions cannot survive removed evidence. Reports expose source links rather than raw excerpts. Current collection has no persistent retry during throttling: it stops on API errors and retries at the next scheduled run. A custom quota/latency agreement may require revising this design before enablement.

Legal entity, contact, approved app identity and data-protection contact: **operator must complete these before submission**. Do not claim a Reddit partnership or approval until received.

## Proposed community scope (not enabled)

| Problem area | Communities to request, subject to approval |
| --- | --- |
| Small-business operations | smallbusiness, Entrepreneur, bookkeeping, ecommerce |
| Professional tools | sysadmin, projectmanagement, graphic_design |
| Home and repair | HomeImprovement, DIY, Appliances |
| Consumer buying decisions | BuyItForLife, VacuumCleaners |
| Hobby equipment | woodworking, gardening, photography, 3Dprinting |

This pilot spans industries; these are hypotheses about useful sources, not validated demand. Community existence, public accessibility and approval must be checked before configuring scope. Extend the source register as communities are approved. Never use `all`, `popular`, arbitrary URLs or private communities as a shortcut.

## Configuration and operation

The adapter lives in `src/agentic_services/niche_reddit.py`. Admin-only manual run: `POST /niche-discovery/v1/admin/collect/reddit`, using the existing `X-Admin-Key`. The public OpenAPI excludes administration routes.

Keep `NICHE_COLLECT_REDDIT=0` until an explicit written commercial agreement and approved API app are available. Required server-only environment variables:

- `NICHE_REDDIT_APPROVAL_REFERENCE`: reference to actual approval; this is an operator attestation, not automatic verification of a license.
- `NICHE_REDDIT_CLIENT_ID`, `NICHE_REDDIT_CLIENT_SECRET`, `NICHE_REDDIT_REFRESH_TOKEN`: approved OAuth application with a permanent read-scope authorization; never place secrets in chat, source control or logs.
- `NICHE_REDDIT_USER_AGENT`: truthful app/version/contact identifier approved by Reddit.
- `NICHE_REDDIT_SUBREDDITS`: explicit comma-separated allowlist, maximum 20.

The adapter uses the official refresh-token endpoint and `oauth.reddit.com` Data API; no anonymous JSON/HTML scraping or third-party archives. It checks only approved communities, stores candidates privately, deduplicates by Reddit fullname, refreshes retained records before acquiring more, and records run statistics. The recent sliding window can miss high-volume activity between polls; increase cadence only within approved limits. It has no historical backfill or full-language coverage claim.

Before enablement: confirm agreement terms and any fees, configure secrets on the server, test one approved community, validate real rate headers/deletion behavior, then broaden to the approved allowlist. No paid Reddit agreement or purchase has been authorized. If approval is revoked, disable collection and purge all Reddit records and dependent reports using the adapter's `purge()` helper, then follow the agreement's backup/log deletion requirements. Current production has no Reddit records.

## Official references

- [Responsible Builder Policy and commercial access contact](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
- [Data API Terms](https://redditinc.com/policies/data-api-terms)
- [OAuth reference maintained in Reddit's archived source repository](https://github.com/reddit-archive/reddit/wiki/OAuth2)
- [Data API endpoint documentation](https://www.reddit.com/dev/api/)
