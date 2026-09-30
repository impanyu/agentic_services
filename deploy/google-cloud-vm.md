# Google Cloud VM deployment

The production hostname is `api.aisoup.net`. It runs on the existing `agentic-wiki` VM in project `impanyu`, zone `us-central1-a`. The host uses Caddy, so the Web Evidence container binds only to `127.0.0.1:8010` and cannot bypass the reverse proxy.

## VM preparation

The VM has Docker Engine, Docker Compose, and Caddy. Its reserved static address is `136.64.166.13`. A dedicated 100 GB balanced persistent disk named `agentic-services-data` is mounted at `/mnt/disks/agentic-services`.

Clone the repository on the VM:

```bash
git clone https://github.com/impanyu/agentic_services.git /mnt/disks/agentic-services/app
cd /mnt/disks/agentic-services/app
```

Create `.env.production` on the VM. Never commit this file:

```dotenv
OPENAI_API_KEY=<OpenAI project key>
OPENAI_MODEL=gpt-6-luna
OPENAI_MAX_TOOL_CALLS=3
OPENAI_MAX_OUTPUT_TOKENS=3000
WEB_EVIDENCE_PROVIDER_CONTACT=<operator email>
WEB_EVIDENCE_API_KEY=<long random internal gateway key>
ADMIN_API_KEY=<long random dashboard and admin API key>
RECEIPT_SIGNING_SECRET=<long random receipt HMAC secret>
WEB_EVIDENCE_DATA_DIR=/mnt/disks/agentic-services/services/web-evidence
WEB_EVIDENCE_SNAPSHOT_DIR=/data/snapshots
WEB_EVIDENCE_PRICE_USD=0.05
WEB_EVIDENCE_QUICK_PRICE_USD=0.02
WEB_EVIDENCE_DEEP_PRICE_USD=0.12
WEB_EVIDENCE_RESEARCH_PRICE_USD=0.25
OPENAI_INPUT_USD_PER_MILLION=0.10
OPENAI_CACHED_INPUT_USD_PER_MILLION=0.01
OPENAI_OUTPUT_USD_PER_MILLION=0.50
OPENAI_WEB_SEARCH_USD_PER_THOUSAND=10.00
PAYMENT_RECIPIENT=<Base-compatible 0x address>
MPP_SECRET_KEY=<at least 32 random bytes>
X402_FACILITATOR_URL=https://facilitator.openx402.ai
# Optional MPP card/USD rail:
STRIPE_SECRET_KEY=<Stripe live secret key>
STRIPE_PUBLISHABLE_KEY=<matching Stripe live publishable key>
STRIPE_NETWORK_ID=<Stripe profile network ID beginning with profile_>
STRIPE_MINIMUM_PRICE_USD=0.50
```

Start the service:

```bash
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
curl http://127.0.0.1:8010/healthz
```

Append the API site block from `deploy/Caddyfile` to `/etc/caddy/Caddyfile`, validate it, and reload Caddy. Caddy obtains and renews TLS automatically after DNS resolves:

```bash
cat deploy/Caddyfile | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## DNS

In Squarespace DNS, create an `A` record:

| Host | Type | Value |
| --- | --- | --- |
| `api` | `A` | `136.64.166.13` |

After DNS resolves, verify:

```bash
curl https://api.aisoup.net/healthz
curl https://api.aisoup.net/.well-known/agent-service.json
```

The public claim endpoint returns HTTP 402 until the caller supplies a valid x402 or MPP payment credential:

```bash
curl https://api.aisoup.net/v1/claims/verify \
  -H 'Content-Type: application/json' \
  -d '{"claim":"OpenAI publishes an official Responses API reference.","minimumSources":1}'
```

Base USDC is always available at the listed tier price. When `STRIPE_SECRET_KEY` is configured, the same response also advertises an MPP Stripe card/USD option. Stripe charges at least `STRIPE_MINIMUM_PRICE_USD` per call; the gateway records that actual amount in the order and receipt.

## Admin key recovery

The admin dashboard is available at `https://api.aisoup.net/admin`. On the operator Mac, run the following helper to copy the production `ADMIN_API_KEY` directly from the VM to the clipboard without displaying it:

```bash
./deploy/copy-admin-key.sh
```

If the key might have been exposed, replace `ADMIN_API_KEY` in `.env.production` with a new random value and recreate the containers. The old key stops working after the restart.
