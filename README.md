# Firmable schema inference

**Latest controlled experiment — 14 October 2026:** generic evidence rebinding and final field-retention constraints passed 478 offline tests. The same-version eight-source run recovered ORIC address components and cost 4.35% less than the previous cohort, but omitted Companies types and changed APRA identifier subjects. New CRICOS/Aged Care holdouts exposed native-reader format and raw-value preservation failures; end-to-end generalization is not established. See the [implementation, results and diagnosis](runs/evidence-retention-20261014/REPORT.zh.md), with the [previous experiment](runs/branch-retention-20261014/REPORT.zh.md) preserved. Experimental outputs do not replace the saved submission cohort.

**Saved acquisition-connected cohort:** the real six-source run retained 22,925 observations, 389 Companies names, 888 BN trading names and 8,184 SOR types. Two tasks completed; four remain partial. Companies type coverage varied from 389 to 386; this small fluctuation was accepted for that submission scope. The 149-row pest/weed source produced no validated config after its official-link fetches failed. See [results](RESULTS.md).

This repository has **one active runtime**. The saved complete six-source cohort in `outputs/` remains bound to `e3061c95bd1865e826e92bc2bdf0a9bfac5017090c547e527341e0f7f4517d98`. Subsequent offline harness repairs have not replaced that cohort: the [mapping quality audit](docs/MAPPING-QUALITY-AUDIT.zh.md) separates confirmed fixes, Agent omissions and insufficient source evidence; the [entity link audit](docs/ENTITY-LINK-AUDIT.zh.md) records later name-role and decision-consistency fixes with their runtime fingerprint. It connects observed metadata/document links to acquisition and freezes original excerpts for the existing inference/revision lifecycle. It retains discovery feedback and subject-risk handling without source-specific mappings. Saved scores are in the [six-source snapshot](evidence/latest-six-source.json), with the [failed holdout](evidence/latest-unseen-source.json) and [validation history](evidence/validation-history.json) retained separately.

Start with the [write-up](WRITEUP.md) ([two-page PDF](output/pdf/WRITEUP.pdf)), [architecture](docs/ARCHITECTURE.md) and [current output summary](outputs/summary.json). The single [evaluation archive](evidence/evaluation-traces.tar.gz) preserves earlier attempts and the full old output cohort under `historical-r2/outputs/`. Its [evidence index](evidence/index.json) and [r2 trace archive](evidence/six-source-r2.tar.zst) describe the historical runtime `bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`. The public API is `firmable_runtime.workflow`.

## Run locally

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/), and Linux or macOS. Installation needs package access.

```sh
uv sync --frozen
uv run python -m firmable_runtime check
uv run python -m firmable_runtime inspect \
  --source-id real-qbcc \
  --evidence-root data/assessment-inputs/real-qbcc/evidence
uv run python -m firmable_runtime build \
  --observations outputs/observations/*.jsonl \
  --output-dir runs/rebuild
```

These commands make no model calls. `check` currently passes 478 offline tests, including acquisition-to-inference integration, mapping semantics, explicit address/identifier formats, identity name roles, evidence binding and field retention, discovery preservation and recovery regressions; the selected snapshot did not retain its original test files. Offline checks establish local behavior, not model quality. Moving old absolute trace paths does not create resumable or newly approved runs.

The older clean-copy receipt (407 tests, 18.429 seconds) applies to the saved runtime, not the current code. The current submission checkout completed installation, all 478 offline tests and rebuilding in 22.881 seconds, including copying the staged files. The current matcher produced 250 groups, 22,222 unlinked records and 50 profiles from the same 22,925 observations; this does not replace the saved 225-group cohort. See [submission verification](evidence/submission-verification.json); no model calls were made. The host dependency cache was available; installation time elsewhere depends on package access.

## Results and remaining work

The current six-source run passed native scope and identity guards for all 22,925 records. Frozen source-agreement labels retained Companies current names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184), with no labelled wrong values or misses. These narrow labels are not human gold. Companies types have coverage of 386 but no independent correctness labels. Legal names remain unresolved for ATO, QBCC, Suitable Operators and ACT.

The current pest/weed holdout identified all 149 native rows but produced no validated config or formal observations; accuracy is N/A. The agent chose official links and used fetch failures in its next decisions, but seven sources together yielded zero new evidence excerpts. Actual fetched-evidence-driven schema improvement remains unproven. The selected holdout also has zero positive mapping labels, limiting what it can measure. Earlier NoWaste success and Cooperatives failure remain bound to their original runtimes in the [validation history](evidence/validation-history.json).

The saved downstream build has 225 linked groups, 22,278 unlinked records and 50 profiles. Its original `outputs/human-review/` pack is preserved. For human review of the repaired identity policy, `runs/entity-link-review/` derives 250 groups and 479 proposed pairs from the same unchanged six-source observations; all 448 old pairs are retained. Its [protocol](runs/entity-link-review/protocol.json) binds the runtime, policy and input hashes. It does not mix in the newer Companies-only run. All config approvals, including the saved cohort, are synthetic. Its original 50-link sample has 0/50 human decisions; link precision remains unknown until the human audit is complete.

The [repaired-policy review page](runs/entity-link-review/human-review/REVIEW.html) now records a completed mixed audit: 18 human UI judgments and 32 user-authorized AI judgments, with 47 pairs supported and three uncertain (29, 35 and 43). The [review receipt](runs/entity-link-review/human-review/delegated-review.json) retains per-item authorship, reasons and evidence bindings. `delegated-reviews.csv` contains the mixed results; `reviews.csv` contains only the 18 human judgments, with other rows blank. Do not import the mixed CSV as 50 human reviews. Run `uv run python scripts/score_review.py --review-dir runs/entity-link-review/human-review` to validate the human-only file; its precision remains null. All 100 sampled endpoints were verified against native snapshots. This sample contains 46 distinct ABN anchors and does not measure ACN-only precision or unlinked-record recall.

## Infer, review and extract a new source

Put native CSV/TSV, nested JSON or XLSX plus original documentation in a new evidence directory. Keep run output outside it. After authorizing paid execution, set `OPENROUTER_API_KEY` through the environment and run:

```sh
uv run python -m firmable_runtime infer \
  --provider openrouter --source-id seventh-source \
  --evidence-root /path/to/native-evidence --run-dir runs/seventh-source \
  --metadata /path/to/catalogue-metadata.json \
  --require-field entity.abn --require-field entity.legal_name
uv run python -m firmable_runtime review --run-dir runs/seventh-source
uv run python -m firmable_runtime approve --run-dir runs/seventh-source \
  --fingerprint FINGERPRINT_FROM_REVIEW --reviewer YOUR_NAME --notes 'Review findings'
uv run python -m firmable_runtime extract \
  --provider openrouter --run-dir runs/seventh-source
```

`infer` makes model calls, including optional reading decisions when source links exist; LLM-backed extraction can also make paid calls. The agent may finish reading without fetching. Only exact original excerpts can supplement inference evidence. Review mappings, evidence and gaps before approving. Omit `--metadata` when unavailable and choose required fields for the task. Requirements are frozen before inference; a partial candidate keeps unresolved requirements through extraction. Without required fields, task acceptance is `not_configured`.

OpenRouter defaults to `anthropic/claude-haiku-5.5`, low reasoning and an Anthropic provider allowlist. `--model` changes the model ID. The CLI streams; the saved r2 evaluation used the Python provider with non-streaming transport. Resume with matching provider/model settings. Unknown paid outcomes stop without automatic resend.

The CLI default is `ccvibe`, requiring `FIRMABLE_MVP_API=1` and the existing `~/.claude/mvp-api/settings.json` profile. Its `--max-budget-usd` is per model call, not a run budget; OpenRouter rejects that option. Never put credentials in tracked files.

## Repository map

| Path | Contents |
|---|---|
| `firmable_runtime/` | Single active runtime |
| `tests/` | Restored offline regression checks |
| `data/catalog/`, `data/selection.json` | Catalogue discovery, ranking and source selection |
| `data/assessment-inputs/` | Six native inputs, original documentation and licences |
| `outputs/configs/`, `outputs/observations/` | Six current model-generated configs and their observations |
| `outputs/` | Current links, unlinked queue, 50 profiles, withdrawal and review pack |
| `evidence/` | Current snapshots, frozen scoring labels, usage history and compact trace archives |
| `data/unseen-source/` | Final holdout input, original metadata, provenance and request |
| `runs/` | Selected reports, metrics, safe receipts, configs and review packets; full experiment working directories are excluded |

## Submission scope and disclosures

The submitted code is the later experimental runtime `9aa20f50`; `outputs/` is the unchanged saved six-source cohort from `e3061c95`. The later experiments remain separate and do not validate those outputs under the current runtime. Frozen absolute paths in journals are provenance; offline rebuilding uses repository-relative inputs, and moving a journal does not authorize resuming a paid call.

The current ontology is the retained assignment subset with an added explanatory note for `address.full`; it is not claimed byte-identical to the original attachment. No ontology change was made within the saved acquisition-connected experiment. Original ontology/config/runtime bindings in each receipt remain unchanged.

The latest author-reported API estimate is US$3.10 and cumulative active effort is approximately 7-10 hours, exceeding the six-hour time box. Historical disclosures are retained in `evidence/historical-cli-costs.json`, including an earlier US$6-7 cost estimate and under-six-hours active-time estimate. Cost scopes have not been reconciled into a verified project total; the latest effort estimate updates the earlier self-report. Provider estimates, actual billed spend and engineering time are distinct.

Known unfinished requirements include the saved cohort's 50-link human precision audit, actual human config approval, reliable new-source onboarding, and a demonstrated real conflict in the submitted 50 profiles. The repository includes selected underlying reports, metric summaries and failure evidence. Full intermediate journals and repeated experimental observations remain local; necessary historical replay fixtures are retained for the offline tests. Source licences and attribution are retained with the native snapshots; internal review drafts, credentials, virtual environments and caches are excluded.
