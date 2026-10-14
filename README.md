# Firmable schema inference

**Latest real run — 14 October 2026:** [six-source results](RESULTS.md) and [tracked evidence](evidence/latest-six-source.json). All 22,925 native observations were produced; two tasks completed and four remained partial. Companies type coverage fell from 389 to zero. The saved r2 outputs below remain the reference cohort.

This repository contains one **pre-scope runtime and its complete six-source repetition 2 (r2) cohort**. All six configs and observations come from the same repetition. The runtime fingerprint is `bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`.

Start with the [evidence index](evidence/index.json), [output summary](outputs/summary.json) and [write-up](WRITEUP.md). Original r2 requests, responses, candidates, synthetic approvals and receipts are retained in the [six-source trace archive](evidence/six-source-r2.tar.zst). The public API is `firmable_runtime.workflow`; see [architecture and contracts](docs/ARCHITECTURE.md).

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

These commands make no model calls. `check` runs the restored offline test collection; the selected snapshot did not retain its original test files. Offline checks establish local behavior and compatibility, not real-model mapping quality. The saved trace archive is for inspection; moving its old absolute run paths does not create resumable or newly approved runs.

## Results and remaining work

All six r2 sources produced their complete retained native population: **22,925 observations**. Companies retained 389/389 labelled current names and Business Names retained 888/888 labelled current business names. **ATO, QBCC, Suitable Operators and ACT still have unresolved legal-name mappings.** Only one of the six r2 onboardings reported complete; five remained partial. Complete native output does not mean complete business mapping.

The broader three-repetition evaluation passed native scope/identity checks on **17/18** trajectories. Repetitions 2 and 3 each had six complete outputs; ATO r1 included six explanatory rows and failed the evaluation's identity guard. Its failure remains in the [original scores](evidence/six-source-scores.json). These are already-seen development sources, not an unseen-source validation.

Rebuilding these observations produces 225 linked entities, 22,278 unlinked records and 50 profiles. All selected config approvals are **synthetic**. The current linked-pair sample has **0 human reviews; precision is unknown**. Review the [50 evidence samples](outputs/human-review/samples.jsonl) and record judgments in the [review CSV](outputs/human-review/reviews.csv). No earlier cohort's 49/50 review applies to these outputs. Mapping coverage, repeatability and human review remain incomplete; this package does not claim submission acceptance or production readiness.

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

`infer` makes model calls; LLM-backed extraction can also make paid calls. Review mappings, evidence and gaps before approving. Omit `--metadata` when unavailable and choose required fields for the task. Requirements are frozen before inference; a partial candidate keeps unresolved requirements through extraction. Without required fields, task acceptance is `not_configured`.

OpenRouter defaults to `anthropic/claude-haiku-5.5`, low reasoning and an Anthropic provider allowlist. `--model` changes the model ID. The CLI streams; the saved r2 evaluation used the Python provider with non-streaming transport. Resume with matching provider/model settings. Unknown paid outcomes stop without automatic resend.

The CLI default is `ccvibe`, requiring `FIRMABLE_MVP_API=1` and the existing `~/.claude/mvp-api/settings.json` profile. Its `--max-budget-usd` is per model call, not a run budget; OpenRouter rejects that option. Never put credentials in tracked files.

## Repository map

| Path | Contents |
|---|---|
| `firmable_runtime/` | Single active runtime |
| `tests/` | Restored offline regression checks |
| `data/catalog/`, `data/selection.json` | Catalogue discovery, ranking and source selection |
| `data/assessment-inputs/` | Six native inputs, original documentation and licences |
| `outputs/configs/`, `outputs/observations/` | Six original r2 configs and observations |
| `outputs/` | Links, unlinked queue, profiles and current summary |
| `evidence/` | Fingerprints, scores, receipts, retained failure and selected run archive |
| `runs/` | Local working output; keep new runs distinct from saved evidence |
