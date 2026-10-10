# Photo Scout intent evaluation

This suite calls the real `parse_intent` function using the configured model and
existing API key. It does not execute Places/OSM queries, geocode locations, score
images, create customer jobs, or alter production configuration.

```sh
PYTHONPATH=src .venv/bin/python scripts/eval-photo-scout-intent.py \
  --cases evals/photo-scout-intent/cases.json \
  --output /tmp/photo-scout-intent-eval.json --repeats 2 --concurrency 4
```

The command makes billable model calls. Credentials come from the normal Settings
configuration and are never printed or saved. Use `--case-ids roof,brand` for a
small subset, or `--model` for an explicitly selected comparison model. Saved
results include the prompt hash, synthetic request, complete returned program,
partial semantic checks, validation diagnostics and request latency.

Checks allow equivalent subject names and appropriate Places/OSM alternatives.
They inspect delivery structure, provider/visual separation, proper names,
AND/OR branch structure, mapped attributes, UI precedence and units. They are
partial predicates, not a complete proof of semantic equivalence. Human review
is required for implicit restrictions, soft preferences, ambiguity, purpose,
provider coverage, access claims and conditional relationships.

The October 9 reports preserve the original two campaigns. The first draft of
the checks overconstrained `cafes`/`coffee shops`, OSM mural retrieval and the
ambiguous Chinese term 素食. `calibrated-checks-2026-10-09.json` rechecks the saved
plans with the improved checks without rerunning the models; raw campaigns are
unchanged. It also detects a weakened pedestrian-bridge constraint that the
original checks missed. Do not report either check percentage as production
accuracy. See the human review for the findings and scope limits.
