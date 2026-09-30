# Google Cloud VM deployment

The production hostname is `api.aisoup.net`. The existing `soup` VM uses host Nginx and Certbot, so the Web Evidence container binds only to `127.0.0.1:8010` and cannot bypass the reverse proxy.

## VM preparation

The current VM is Ubuntu 22.04 LTS with Docker Engine, Docker Compose, Nginx, and Certbot. Its reserved static address is `34.28.188.78`.

Clone the repository on the VM:

```bash
git clone https://github.com/impanyu/agentic_services.git
cd agentic_services
```

Create `.env.production` on the VM. Never commit this file:

```dotenv
OPENAI_API_KEY=<OpenAI project key>
OPENAI_MODEL=gpt-6-luna
WEB_EVIDENCE_PROVIDER_CONTACT=<operator email>
WEB_EVIDENCE_API_KEY=<long random internal gateway key>
WEB_EVIDENCE_PRICE_USD=0.05
PAYMENT_RECIPIENT=<Base-compatible 0x address>
MPP_SECRET_KEY=<at least 32 random bytes>
X402_FACILITATOR_URL=https://facilitator.openx402.ai
# Optional MPP card/USD rail:
STRIPE_SECRET_KEY=<Stripe live secret key>
STRIPE_NETWORK_ID=agentic-services
```

Start the service:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8010/healthz
```

Install the Nginx virtual host and request TLS after DNS resolves:

```bash
sudo cp deploy/nginx-api.conf /etc/nginx/sites-available/agentic-services-api
sudo ln -s /etc/nginx/sites-available/agentic-services-api /etc/nginx/sites-enabled/agentic-services-api
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d api.aisoup.net --non-interactive --agree-tos --redirect -m "$WEB_EVIDENCE_PROVIDER_CONTACT"
```

## DNS

In Squarespace DNS, create an `A` record:

| Host | Type | Value |
| --- | --- | --- |
| `api` | `A` | `34.28.188.78` |

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

Base USDC is always available. When `STRIPE_SECRET_KEY` is configured, the same response also advertises an MPP Stripe card/USD option.
