"""Distribute the shared measurement script to independently served static sites."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
content = (root / "company-site/assets/usage.js").read_bytes()
for site in ("web-evidence-site", "contractor-check-site", "niche-discovery-site"):
    (root / site / "usage.js").write_bytes(content)
