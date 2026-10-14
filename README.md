# Firmable schema inference

**Latest validation — 14 October 2026:** the final six-source run recovered BN trading names (888) and Companies type coverage (389), with all 22,925 native observations retained. Two tasks completed; four remain partial. The frozen-runtime Cooperatives holdout failed to produce a validated config, so generalisation and repeatability remain unproven. See [results](RESULTS.md).

This repository has **one active runtime**, fingerprint `18984daebde13d47999f8bdbc3b4758074db27e0159799ee4506c185ff9e75b3`, and the complete historical r2 reference in `outputs/`. The current runtime improves mapping-discovery context, repair observations and subject-risk handling; it does not add source-specific mappings. Current configs and scores are in the [six-source snapshot](evidence/latest-six-source.json), with the [failed holdout](evidence/latest-unseen-source.json) and [all attempted validation costs/results](evidence/validation-history.json) retained separately.

Start with the [write-up](WRITEUP.md), [architecture](docs/ARCHITECTURE.md) and [current evaluation archive](evidence/evaluation-traces.tar.gz). The older [evidence index](evidence/index.json), [output summary](outputs/summary.json) and [r2 trace archive](evidence/six-source-r2.tar.zst) describe the unchanged historical reference, whose runtime was `bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`. The public API is `firmable_runtime.workflow`.

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

These commands make no model calls. `check` currently passes 394 offline tests, including the restored collection and new harness regressions; the selected snapshot did not retain its original test files. Offline checks establish local behavior and compatibility, not real-model mapping quality. The saved trace archive is for inspection; moving its old absolute run paths does not create resumable or newly approved runs.

## Results and remaining work

The final six-source run passed native scope and identity guards for all 22,925 records. Frozen source-agreement labels retained Companies current names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184), with no labelled wrong values or misses. These narrow labels are not human gold. Companies types have coverage of 389 but no independent correctness labels. Legal names remain unresolved for ATO, QBCC, Suitable Operators and ACT.

The final Cooperatives holdout identified all 17 native rows but produced no validated config or formal observations; accuracy is N/A. Its missing field definitions and disconnected metadata/acquisition path exposed a remaining harness limitation. An earlier attempt's successful NoWaste holdout belongs only to that earlier runtime, which also lost all BN trading names. Both outcomes remain visible in the [validation history](evidence/validation-history.json); no best-per-source mixing is used.

The final downstream build has 225 linked groups, 22,278 unlinked records and 50 profiles. The 29 previously identified risk records remain unlinked. Config approvals are synthetic; there are zero human link reviews and precision is unknown. The saved 50 review samples under `outputs/human-review/` belong to historical r2 and must not be presented as human judgments of the current run. This repository does not claim submission acceptance or production readiness.

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
| `outputs/` | Historical r2 links, unlinked queue, profiles and summary |
| `evidence/` | Current snapshots, frozen scoring labels, usage history and compact trace archives |
| `data/unseen-source/` | Final holdout input, original metadata, provenance and request |
| `runs/` | Two fixed current evaluation directories; full journals also archived |
