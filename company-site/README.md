# Dream Workshop company site

The public site at `https://aisoup.net` is generated from a small product catalog.

To add a future agent or service:

1. Add one object to `catalog.json`.
2. Run `python3 company-site/build.py` from the repository root.
3. Commit the source. Rebuild `dist` on the host before reloading Caddy.

Each catalog item has a canonical human URL at `https://aisoup.net/<slug>/` and an `audience` of `human`, `agent`, or `both`. Agent API and MCP links belong on `https://api.aisoup.net/<slug>/`. AgenticWiKi uses `https://aisoup.net/wiki/` as its catalog entry, which redirects to the existing `https://wiki.aisoup.net/` application.

The host Caddy server serves `company-site/dist` directly, so the company site does not consume a container port or affect the existing applications.
