# Photo Scout image-model comparison — October 11, 2026

Two controlled input cases were submitted to the direct Images edit API with the project's existing credential. Inputs, exact current production prompt, output resolution (1024×1024), and other options were held constant within each case. At most two API calls ran concurrently; automatic retries were disabled. Tests ran independently of website jobs/history and did not change production configuration.

The cases are an Einstein-style preset avatar on a waterfront terrace and the existing SpongeBob preset in the Chengdu panda-sculpture plaza. They do not represent general customer uploads, groups, animals, all lighting conditions, or a production latency distribution. PNG outputs and the source images are local comparison artifacts; no private user portraits were used.

## Fresh measured results

| Case | Model | Quality | Generation API time | Estimated successful edit cost | Outcome |
|---|---|---|---:|---:|---|
| Waterfront / real-person preset | Sunburst | max | 113.105 s | $0.242810 | Complete |
| Waterfront / real-person preset | Sunburst | high | 34.610 s | $0.084770 | Complete |
| Waterfront / real-person preset | Flare | xhigh | 29.853 s | $0.125750 | Complete |
| Plaza / cartoon preset | Sunburst | max | 104.542 s | Unknown | moderation_blocked; no image |
| Plaza / cartoon preset | Sunburst | high | 37.673 s | $0.075887 | Complete |
| Plaza / cartoon preset | Flare | xhigh | 28.294 s | Unknown | moderation_blocked; no image |

Costs use returned token counts and official standard token rates, including both input images and text. They exclude subject checks, background preparation/analysis, source-provider calls, queue time and downloads. They are estimates rather than invoices. Do not treat unsuccessful requests as free: billed usage was not returned. Request IDs are retained in the JSON record. No moderation category or specific cause was returned in the captured diagnostics; do not infer that a particular character is forbidden, that quality settings cause blocks, or that these two cases establish a model's general success rate. Blocked requests were not retried.

For the completed waterfront pair, Sunburst high reduced generation time by 69.4% and estimated generation cost by 65.1% against max. Flare xhigh reduced generation time by 73.6% and cost by 48.2%. High's two successful cases averaged 36.142 seconds and $0.080328; max and Flare each have only one successful fresh observation. These are tiny-sample generation measurements, not new production average task costs/times.

A separate, earlier local waterfront comparison recorded Sunburst max 116.5 s/$0.24281, high 33.2 s/$0.08477, xhigh 57.8 s/$0.12575, medium 17.2 s/$0.04526, Flare xhigh 26.5 s/$0.12575, and Flare high 21.0 s/$0.08477. It is supporting historical evidence only and is not mixed into the fresh-case averages above.

## Quality review and decision

Direct inspection found coherent subjects, plausible contact/shadows and recognizable identities in all completed waterfront outputs. Sunburst high's plaza output retained the panda sculpture and recognizable surrounding buildings. However, waterfront outputs from **all three tested configurations** reconstruct aspects of the source scene rather than preserving exact pixels and geometry: bridge silhouettes, railing posts, planters, building openings and their framing change. Max is not immune to this issue. Sharper output alone does not establish background fidelity.

A separate GPT-6.1 Sol reviewer received the source subject/background and outputs labelled A/B/C without model/price/tier labels. It rated background fidelity on a 0–5 scale as Flare xhigh 2, Sunburst max 3, and Sunburst high 2; its concrete findings align with the visual concern above. This is one automated review of one case, not an objective quality ranking or proof of equivalence. There was no full three-way plaza review because two outputs were blocked.

Sunburst high is the leading inexpensive candidate for a broader quality gate; Flare xhigh is a speed-focused candidate. Neither has demonstrated quality-equivalent replacement across the supported product use cases. **Production remains Sunburst max.** A stronger background-preservation method and representative tests for groups, animals, cartoons, fixed landmarks and differing light are needed before switching the default. No automatic paid retry/fallback or second full image generation was added.

## Other providers, researched but not run

- **Gemini Nano Banana 2.1 (`gemini-nano-banana-2.1`)**: official pricing lists $0.0336 for a 1K output image, plus text/image inputs and any text/thinking output. This is not the total cost of a two-image edit. An editing-oriented candidate for a same-input test, but no usable Gemini image API credential was found in the two project env files checked; no requests were made and no new key was provisioned.
- **Gemini 3.1 Flash Lite Image**: also $0.0336 for 1K output; its official description prioritizes latency/cost. Its two-image input costs and quality must be measured separately. Gemini 3.1 Flash Image lists about $0.067 per 1K output. These options were not tested here.
- **Seedream 5.0 Flash / Pro**: official image-generation API listings identify multi-reference image support. Worth evaluating for the China-market deployment path. Official pricing page content could not be fully retrieved during this pass, so an indexed price snippet is not used as a verified budget or replacement recommendation. No usable Ark key was found in the checked project env files.
- **FLUX.2 multi-reference editing**: official BFL documentation supports combining references. Its cost depends on the particular model and input/output dimensions; do not substitute a generic text-to-image headline price for this two-input edit. No BFL API calls were made. Provider credential availability beyond the checked OpenAI/Gemini/Ark fields was not established.

Sources, checked October 11, 2026:

- [Sunburst model and token prices](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)
- [Flare model](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)
- [OpenAI image-generation guide](https://developers.openai.com/api/docs/guides/image-generation/index.html)
- [Gemini official pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini image generation/editing](https://ai.google.dev/gemini-api/docs/image-generation)
- [Seedream image-generation API](https://docs.byteplus.com/en/docs/ModelArk/image-generation-api)
- [BFL multi-reference editing](https://help.bfl.ai/articles/6546682167-what-is-multi-reference-editing)
- [BFL pricing](https://bfl.ai/pricing?category=flux.2)

## Reproduction

`scripts/benchmark-photo-scout-images.py` accepts a JSON list of cases with `id`, PNG `subject`, PNG/JPEG `background`, `place`, background `scene` inventory and optional `pose`. Paths are relative to the manifest. It calls the same `portrait_prompt` used in production, records input/prompt hashes, returned usage, request IDs and API elapsed time, and refuses to overwrite an output directory. Default variants are Sunburst max/high and Flare xhigh. This script makes paid requests: invoking it is explicit, not part of ordinary tests or production jobs.

```sh
.venv/bin/python scripts/benchmark-photo-scout-images.py /path/to/cases.json /new/output/directory
```

Raw fresh measurements: `evals/photo-scout-images/quality-2026-10-11.json`. Blind-review aid: `evals/photo-scout-images/review-2026-10-11.json`. Local full-resolution images and comparison page: `/tmp/photo-scout-quality-benchmark/`. Script validation: CLI help and Python compilation passed; no production code changed, so the full service regression suite was not repeated.
