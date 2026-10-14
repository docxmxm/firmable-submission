# Six source real model results

**14 October 2026.** One fresh run of each of the six retained development sources produced all **22,925 native observations**. Onboarding and task status were **2 complete and 4 partial**. The three narrowly labelled fields retained all expected values, but Companies entity-type coverage fell from 389 in the saved r2 reference to zero. The run demonstrates successful execution with persistent mapping gaps; it does not establish stable or unseen-source performance.

The [tracked result snapshot](evidence/latest-six-source.json) records the final protocol, measurements and scores. The saved r2 cohort in `outputs/` remains a separate reference; this fresh run lives in `runs/six-source/`.

## Run conditions

The runtime fingerprint was `bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`. Model calls used `anthropic/claude-haiku-5.5`, the Anthropic provider allowlist, low reasoning, non-streaming transport and three concurrent workers. Each source ran once. Runtime, native inputs, documentation, ontology and frozen evaluation labels remained unchanged throughout execution. Labels were used only by the scorer, not supplied to the planner.

Every config was technically validated. Companies and Business Names completed their declared tasks; ATO, QBCC, Suitable Operators and ACT remained partial because their legal-name requirements were unresolved. All approvals were explicitly synthetic. Technical completion is separate from human acceptance and field completeness.

## Core field coverage

Counts below are informative emitted values; `unknown` is excluded. Zero means no informative output for that field, not that every source row necessarily supports it. Apart from the labelled fields discussed below, these are coverage counts rather than accuracy scores.

| Source | Native rows / observations | ABN | ACN | Current name | Entity type | Address |
|---|---:|---:|---:|---:|---:|---|
| ASIC Companies | 389 / 389 | 389 | 389 | 389 legal | **0** | 0 |
| ASIC Business Names | 1,000 / 1,000 | 802 | 0 | 888 trading | 0 | 0 |
| ATO Corporate Tax | 4,436 / 4,436 | 4,400 | 0 | 0 | 0 | 0 |
| QBCC | 1,000 / 1,000 | 623 | 326 | 0 | 0 | 1,000 full source addresses |
| Suitable Operators | 15,100 / 15,100 | 299 | 7,901 | 0 | 8,184 | 14,825 each: locality, state, postcode |
| ACT historical contracts | 1,000 / 1,000 | 759 | 0 | 0 | 0 | 0 |

Companies also emitted 389 registration dates. All six passed the scorer's native population and identity guards. Companies entity-type coverage was **389 → 0** relative to saved r2 despite unchanged code and inputs. Names remaining correct does not remove that coverage regression.

## What the labels establish

- Companies current names: **389/389 correct**, zero labelled wrong values or misses.
- Business Names current trading names: **888/888 correct**, zero labelled wrong values or misses.
- Suitable Operators entity types: **8,184/8,184 correct**, zero labelled wrong values or misses.
- Suitable Operators abstention checks: **6,714/6,714** labelled type abstentions and **15,100/15,100** labelled legal-name abstentions, with **zero unsupported informative emissions** in those checks.

These frozen source-agreement labels are not human gold. They support the listed field-level results only. ATO, QBCC and ACT have coverage measurements without an accuracy score; unlabelled fields are not counted as correct. No pooled or global “100% accuracy” is claimed. Single-run agreement does not prove stability, and all six sources were already used during development.

## Cost and downstream output

The run made **75 model calls**, with **1,427,814 reported tokens** and **USD 0.1914494** in provider-returned costs. All 75 receipts reported success and a cost; no unknown outcome was recorded. End-to-end concurrent wall time was **220.761 seconds**, including the runner's final deterministic build. It is not engineering time, a sum of source durations or total historical project spend. Provider receipts are not invoices. Formal extraction used AST with zero per-record model calls.

The deterministic downstream build produced **232 linked entity groups**, **22,249 unlinked records** and **50 profiles** at the fixed 0.99 rule threshold. That score is uncalibrated. There are **zero human link reviews and zero human-approved configs** for this run; link precision remains unknown. Earlier cohorts' labels, including the historical 49/50 review, cannot be inherited. A larger linked population is not evidence of better resolution.

## Reproduction and retained evidence

After paid execution is authorized, run from a checkout without an existing `runs/six-source` directory. Supply `OPENROUTER_API_KEY` through the environment or the runner's hidden prompt:

```sh
uv sync --frozen
uv run python scripts/run_six_sources.py
uv run python scripts/score_six_sources.py runs/six-source
```

The runner refuses to overwrite an existing run and stops new requests after an unknown outcome. Its USD 2 receipt-based guard with reservations is a soft local limit, not a provider-enforced billing cap. Scoring is offline and may be rerun on the retained directory without a new model call.

Full local requests, responses, candidates, reviews, approvals, observations and receipts are retained under the single `runs/six-source/` directory, which is ignored by Git. The committed [latest-run snapshot](evidence/latest-six-source.json) preserves the reviewable protocol, final measurements and scoring evidence without requiring those local journals. This run does not add an unseen-source test, repeated-run stability evidence or human precision measurement.
