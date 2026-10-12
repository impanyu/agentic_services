# Photo Scout points and billing

Version: 2026-10-v1. 100 points = $1 for a one-time pack. Tasks charge recorded
provider API usage × standard list price × 3 × 100 points/USD, rounded upward
at 0.001-point precision per item. Unknown usage is displayed and not charged.
Provider invoices, discounts, taxes and credits are not reconciled automatically;
these bills are a disclosed metering basis, not provider invoices.

One-time packs: $5 / 500 points, $10 / 1,000, $25 / 2,500.
Monthly plans: $10 / 1,200, $25 / 3,250, $50 / 7,000. Each successful monthly
payment grants a finite allowance; unused points accumulate without expiry.
No unlimited tier. Cancellation stops future renewal. The Stripe customer portal
allows cancellation and payment-method updates but no prorated plan changes.

Only signed-in accounts can start point-funded jobs or buy points. Guests may
browse the map, public places, examples and avatar catalog. Existing guest history
and uploads retain their existing seven-day retention and account migration.
No historical free tasks are charged retrospectively.

Task submission freezes the selected limit atomically with job admission. Defaults
are 250 points for search and 100 for photos, adjustable from 10 to 2,000 in Profile.
Completion charges the itemized sum, capped at the reservation; any cost beyond
the cap is absorbed by the service and recorded as a discount. Failure charges zero.
The cap controls the user charge, not the workflow quality or provider budget.
Running jobs and crash recovery retain holds; terminal jobs reconcile on wallet reads.

Credits and bills use independent permanent tables; deleting a result never deletes
its financial record. Funding requires server-verified paid Checkout / invoice or
Apple-signed production transactions bound to the signed-in appAccountToken.
Unique ledger references prevent duplicate funding, debits and refund reversals.

## Deployment

Enable `PHOTO_SCOUT_CREDITS_ENABLED=1` after migration and the owner gift are verified.
Stripe needs a dedicated server-only restricted key, wallet webhook secret, and a
customer portal configuration. Required API permissions: Checkout Sessions write;
Customers, Products and Prices write; Payment Intents as required by Checkout;
Invoices and Subscriptions read; Charges and Refunds read; Customer Portal write;
Webhook Endpoints write for one-time setup. No payout/transfer/Issuing permissions.
Store secrets only in protected production configuration, never source control.

Webhook: `https://photo-scout.aisoup.net/api/v1/billing/webhook`.
Events: checkout.session.completed, checkout.session.async_payment_succeeded,
invoice.paid, customer.subscription.updated, customer.subscription.deleted,
refund.created and refund.updated. HTTP signature verification precedes provider
retrieval. Test notifications and test payments never mint production points.

Apple requires App Store Connect products and server notifications before enabling
`PHOTO_SCOUT_APPLE_IAP_ENABLED=1` and `PHOTO_SCOUT_APPLE_APP_ID`. Consumable products:
`net.aisoup.photoscout.points_500`, `points_1000`, `points_2500` (same prefix).
Auto-renewable monthly products: `net.aisoup.photoscout.monthly_1200`,
`monthly_3250`, `monthly_7000` (same prefix), in one subscription group.
Prices must be configured in App Store Connect. The app displays StoreKit's localized
price, never substitutes the web price. Production rejects sandbox transactions.
Apple notification endpoint: `/api/v1/billing/apple/notifications`.
Purchase completion waits for server funding before finishing the StoreKit transaction.

A requested owner gift uses a fixed idempotent ledger reference and is not recorded
as a cash payment. Real payment-to-wallet verification must be reported separately
from mocked API tests, Checkout page rendering or code compilation.
