# Dream Workshop company site

The public site at `https://aisoup.net` is generated from a small product catalog.

To add a future agent or service:

1. Add one object to `catalog.json`.
2. Run `python3 company-site/build.py` from the repository root.
3. Commit the source and generated `dist` files, then deploy as usual.

The host Caddy server serves `company-site/dist` directly, so the company site does not consume a container port or affect the existing applications.
