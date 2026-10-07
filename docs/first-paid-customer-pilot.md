# First external paid customer pilot

Primary offer: Web Evidence, one $2 Standard claim report for newsletter writers,
editors and researchers checking a factual statement before publication. Existing
prices remain unchanged. A free archived internal verification demonstrates the
format; it is not a testimonial, a fresh check or evidence of customer demand.

## Ready-to-use links

- Sample and purchase: https://aisoup.net/web-evidence/?utm_source=community&utm_campaign=writer-pilot
- Social channel: https://aisoup.net/web-evidence/?utm_source=social&utm_campaign=writer-pilot
- Authorized email outreach: https://aisoup.net/web-evidence/?utm_source=email&utm_campaign=writer-pilot
- Developer channel: https://aisoup.net/web-evidence/?utm_source=community&utm_campaign=developer-pilot
- Free sample: https://aisoup.net/web-evidence/sample/
- Early niche evaluations: https://aisoup.net/niche-discovery/research-notes/

Source is an approved broad category; no arbitrary personal names or full URLs
are stored. A source link must be the first service visit in the tab to set its
first-touch attribution. Sessions are not people, and unmarked activity is not
verified external activity. Do not claim four historical Checkout intents are
four prospects. Measurement starts with this deployment, without backfilling.

## Ten-person initial cohort

Recruit manually, following venue rules, from these roles:

| Role | Initial count | Selection criterion | Validation question |
| --- | ---: | --- | --- |
| Independent newsletter writers | 4 | Regularly publish source-linked factual articles | Which statement in your next issue needs checking? |
| Freelance editors or researchers | 4 | Have an active document requiring source review | What would make this report reusable in your current job? |
| Agent workflow developers | 2 | Maintain a research workflow and can configure payment | Can a paid verification replace a current workflow step? |

Use existing permission-based contacts first. If using communities, check their
current promotion rules and use designated feedback/showcase spaces. No specific
person is claimed to have agreed, no recruitment message has been sent, and no
ads or directory upgrades are purchased by this implementation.

## Draft invitation (not sent)

> I’m testing Web Evidence for people checking factual statements before they
> publish. You can inspect a real archived sample for free, then check one of
> your own claims for $2. The report includes a dated assessment, source links
> and limitations; uncertainty is a possible result. If you have a statement
> in your next article that needs checking, would this fit that task?
> https://aisoup.net/web-evidence/?utm_source=email&utm_campaign=writer-pilot

## Measurement and acceptance

Open https://api.aisoup.net/admin with the existing admin key. The funnel table
uses 30-day data, filterable by service and period. Each stage is deduplicated.

- `page_view`, `sample_view`, `checkout_attempt`, `report_view`: untrusted browser
  observations, one event per stage per browser-tab session and service.
- `checkout_created`: server successfully created and bound a Stripe session.
- `checkout_failed`: server returned an error to a marked browser-session checkout
  request. This is not evidence of payment failure or card decline.
- `payment_confirmed`: backend retrieved and validated paid status, currency,
  amount, service binding, mode and live/test mode before generating a report.
- `report_ready`: complete result available, including webhook fulfillment when
  the buyer never visits the return page.
- `report_delivered`: report endpoint served the verified purchase result.
- `report_view`: browser says it rendered the report; it can be absent if optional
  measurement is disabled. These two delivery observations do not prove reading.

Use “Open marked internal test” in the dashboard before testing. The button issues
a one-hour HMAC-signed token in the URL fragment (not the request query) and the
service immediately removes the fragment. The token contains no admin key.
Self-declared `internal` headers do not work. Legacy unattributed events stay
unknown; they are not reclassified from timing alone. Measurement errors do not
block commerce. Payments without session measurement remain unattributed financial
events. Browser Do Not Track, GPC and a persistent local opt-out are honored.

Success requires **one independently confirmed, external, live paid order and a
usable delivered result**. Reconcile it with Stripe and the internal order ledger,
confirm it was not operator testing, and record whether the buyer would use it
again. No synthetic subscriber, self-payment or directory listing counts.

After ten qualified invitations, evaluate each stage separately. No visits means
the audience/channel failed to reach people; sample views without checkout attempts
suggest a value mismatch; attempts without created sessions suggest flow problems;
created sessions without confirmed payment require buyer feedback and Stripe status;
payment without report readiness is a fulfillment failure. These are diagnostic
hypotheses, not causal conclusions from a tiny sample. Do not reduce prices or buy
traffic before inspecting the evidence.

## Niche Discovery content

Three free, dated early evaluations include source links, buyer, problem, possible
offer, competition, counterevidence and a concrete validation test. They are kept
separate from the reviewed paid database. They do not bypass publication gates,
create measured market-size figures or make unsupported growth forecasts.

The operator objective asks the autonomous manager to build a first evidence-backed
batch within its current daily limits, prioritize completion and external working
memory, and preserve rejected candidates and weak evidence privately. Budget is
not raised. Until credible records exist and live subscription delivery is verified,
human subscriptions and empty-catalog paid searches remain closed.
