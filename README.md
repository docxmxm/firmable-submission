# Firmable schema inference

**Latest validation — 14 October 2026:** agent-directed source-link acquisition is connected. The real six-source run retained 22,925 observations, 389 Companies names, 888 BN trading names and 8,184 SOR types. Two tasks completed; four remain partial. Companies type coverage varied from 389 to 386; this small fluctuation is accepted for the current submission scope. The new 149-row pest/weed source produced no validated config after its official-link fetches failed. See [results](RESULTS.md).

This repository has **one active runtime**, fingerprint `e3061c95bd1865e826e92bc2bdf0a9bfac5017090c547e527341e0f7f4517d98`, and the complete current six-source cohort in `outputs/`. It connects observed metadata/document links to acquisition and freezes original excerpts for the existing inference/revision lifecycle. It retains discovery feedback and subject-risk handling without source-specific mappings. Current scores are in the [six-source snapshot](evidence/latest-six-source.json), with the [failed holdout](evidence/latest-unseen-source.json) and [validation history](evidence/validation-history.json) retained separately.

Start with the [write-up](WRITEUP.md), [architecture](docs/ARCHITECTURE.md) and [current output summary](outputs/summary.json). The single [evaluation archive](evidence/evaluation-traces.tar.gz) preserves earlier attempts and the full old output cohort under `historical-r2/outputs/`. Its [evidence index](evidence/index.json) and [r2 trace archive](evidence/six-source-r2.tar.zst) describe the historical runtime `bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`. The public API is `firmable_runtime.workflow`.

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

These commands make no model calls. `check` currently passes 407 offline tests, including acquisition-to-inference integration and recovery regressions; the selected snapshot did not retain its original test files. Offline checks establish local behavior, not model quality. Moving old absolute trace paths does not create resumable or newly approved runs.

A clean copy with no existing virtual environment completed installation, all 407 tests and output rebuilding in 18.429 seconds on the development machine. The host dependency cache was available; installation time elsewhere depends on package access. Rebuilding reproduced 22,925 observations, 225 groups and 50 profiles without API calls.

## Results and remaining work

The current six-source run passed native scope and identity guards for all 22,925 records. Frozen source-agreement labels retained Companies current names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184), with no labelled wrong values or misses. These narrow labels are not human gold. Companies types have coverage of 386 but no independent correctness labels. Legal names remain unresolved for ATO, QBCC, Suitable Operators and ACT.

The current pest/weed holdout identified all 149 native rows but produced no validated config or formal observations; accuracy is N/A. The agent chose official links and used fetch failures in its next decisions, but seven sources together yielded zero new evidence excerpts. Actual fetched-evidence-driven schema improvement remains unproven. The selected holdout also has zero positive mapping labels, limiting what it can measure. Earlier NoWaste success and Cooperatives failure remain bound to their original runtimes in the [validation history](evidence/validation-history.json).

The current downstream build has 225 linked groups, 22,278 unlinked records and 50 profiles. The 29 previously identified risk records remain unlinked. Config approvals are synthetic; there are zero human link reviews and precision is unknown. The refreshed 50-pair review pack under `outputs/human-review/` is bound to this current cohort and awaits actual human decisions. This missing Part 3 measurement remains an explicit submission gap.

Open [the local review page](outputs/human-review/REVIEW.html) in a browser, inspect both raw records and their source locators, and fill each judgment and reviewer. Export `reviews.csv` into `outputs/human-review/`, then run `uv run python scripts/score_review.py`. The script validates the frozen sample and evidence; it does not generate judgments. It reports precision only after all 50 judgments are supplied and counts uncertain cases as unconfirmed, not correct.

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
| `runs/` | Two fixed current evaluation directories; full journals also archived |
