#!/usr/bin/env python3
"""Build the AI Soup static site from the product catalog."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"


def product_card(product: dict[str, object]) -> str:
    audience = str(product["audience"])
    human = "✓" if audience in {"human", "both"} else "—"
    agent = "✓" if audience in {"agent", "both"} else "—"
    tags = "".join(
        f'<li>{html.escape(str(tag))}</li>' for tag in product["capabilities"]
    )
    return f"""
      <article class="product-card product-card--{html.escape(str(product['accent']))}">
        <a class="product-card__link" href="{html.escape(str(product['url']))}" aria-label="{html.escape(str(product['cta']))}">
          <div class="product-card__top">
            <span>{html.escape(str(product['eyebrow']))}</span>
            <span class="status"><i></i> {html.escape(str(product.get('status', 'Live')))}</span>
          </div>
          <div class="product-card__body">
            <span class="product-number">{html.escape(str(product['number']))}</span>
            <h3>{html.escape(str(product['name']))}</h3>
            <p>{html.escape(str(product['description']))}</p>
            <p class="product-audience"><span>{human} For people</span><span>{agent} For agents</span></p>
          </div>
          <div class="product-card__bottom">
            <ul>{tags}</ul>
            <span class="arrow" aria-hidden="true">↗</span>
          </div>
          <div class="product-card__action">
            <span>{html.escape(str(product['cta']))}</span>
            <code>{html.escape(str(product['domain']))}</code>
            <b aria-hidden="true">↗</b>
          </div>
        </a>
      </article>"""


def main() -> None:
    catalog = json.loads((ROOT / "catalog.json").read_text())
    products = catalog["products"]
    cards = "\n".join(product_card(product) for product in products)

    template = (ROOT / "index.template.html").read_text()
    website_json_ld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "AI Soup",
        "legalName": "Dream Workshop LLC",
        "url": "https://aisoup.net",
        "description": "Agent for App: agents inside apps for people. App for Agent: apps, tools, and services for agents.",
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "Products",
            "itemListElement": [
                {
                    "@type": "Offer",
                    "itemOffered": {
                        "@type": "SoftwareApplication",
                        "name": product["name"],
                        "url": product["url"],
                        "description": product["description"],
                    },
                }
                for product in products
            ],
        },
    }
    page = (
        template.replace("{{ALL_PRODUCT_CARDS}}", cards)
        .replace("{{JSON_LD}}", json.dumps(website_json_ld, ensure_ascii=False))
    )

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    (DIST / "index.html").write_text(page)
    shutil.copytree(ROOT / "assets", DIST / "assets")
    shutil.copy(ROOT / "robots.txt", DIST / "robots.txt")
    shutil.copy(ROOT / "sitemap.xml", DIST / "sitemap.xml")
    shutil.copy(ROOT / "favicon.svg", DIST / "favicon.svg")
    shutil.copy(ROOT / "catalog.json", DIST / "catalog.json")
    print(f"Built {len(products)} products into {DIST}")


if __name__ == "__main__":
    main()
