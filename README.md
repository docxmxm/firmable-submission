# Firmable schema inference

The current submission uses one generic runtime (`9aa20f50`) and six unchanged model-generated configs. Replaying the original native snapshots produces **22,925 observations, 250 proposed entity groups, 479 cross-source pairs, 22,222 unlinked records and 50 profiles**. Code and submitted observations/downstream artifacts are checked for byte-for-byte reproducibility.

The fixed 50-link human audit is complete: **44 correct, one incorrect and five uncertain** at threshold **0.99**, an uncalibrated policy score. The conservative confirmed-correct fraction is **44/50 = 88%**; uncertain decisions remain in the denominator. The incorrect and uncertain proposals remain visible. Profiles are proposed merges, not human-approved clusters. See the [human audit](outputs/human-review/summary.json) and [confirmation receipt](outputs/human-review/CONFIRMATION-RECEIPT.json).

Start with the [two-page write-up](WRITEUP.md) ([PDF](output/pdf/WRITEUP.pdf)), [current results](RESULTS.md), [architecture](docs/ARCHITECTURE.md) and [current version/evidence binding](evidence/current-submission.json).

## Run locally

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/), and Linux or macOS. Installation needs package access.

```sh
uv sync --frozen
uv run python -m firmable_runtime check
uv run python scripts/score_review.py --check-only
uv run python -m firmable_runtime build \
  --observations outputs/observations/*.jsonl \
  --output-dir runs/rebuild
```

These commands make no model calls. The final clean checkout passed 481 tests and completed installation, checks, human-audit validation and rebuilding in 31.529 seconds. The offline checks include replaying all six configs against native inputs, comparing every observation byte, rebuilding links/profiles/the unlinked queue/withdrawal, and validating the complete human audit. The clean-checkout timing and test count are recorded in [submission verification](evidence/submission-verification.json). The host dependency cache was available; installation time elsewhere depends on package access. Offline reproducibility does not establish mapping accuracy.

## Version and audit lineage

The configs were inferred under runtime `e3061c95`. The current runtime replays them without editing mappings or native rows. Five source outputs are byte-identical to that earlier run. For Suitable Operators, the current generic address parser adds three original-value components on 183 records (549 values); no previous canonical values, raw records, locators, identifiers or snapshot ingestion times changed. Component agreement does not establish identifier ownership or address semantics.

All 479 pair contracts and the 100 sampled observation objects are unchanged from the repaired-policy review packet. Its 18 human judgments were preserved, and the reviewer completed the remaining 32 in an interface with blank decisions and no AI prefill. The separate historical mixed AI/human audit is excluded from the current human precision. This sample has 46 distinct ABN anchors; it does not measure ACN-only precision or unlinked-record recall.

The previous 225-group submission remains recoverable at Git commit `f673ff961b8cceb7afb3cf2fdeaa96998d0ed46e`. Historical acquisition receipts, [six-source snapshot](evidence/latest-six-source.json), [validation history](evidence/validation-history.json) and [evaluation archive](evidence/evaluation-traces.tar.gz) retain their original runtime and artifact identities. Paths in those historical receipts describe that revision, not the current `outputs/`. Earlier r2 artifacts remain under `historical-r2/outputs/` in the archive, with their [index](evidence/index.json) and [trace archive](evidence/six-source-r2.tar.zst). Moving old journals does not authorize resuming paid calls.

## Results and limitations

The catalogue contains 1,290 retrieved records, a programmatically ranked shortlist of 50, and a fixed random 20-item human-confirmed, AI-assisted audit (19 positive, one uncertain). The possible sample false-positive fraction is 0-5%, not a population estimate.

Frozen narrow labels retain Companies names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184). These are source-agreement checks, not human gold or whole-system accuracy. Legal names remain unresolved for ATO, QBCC, Suitable Operators and ACT. All config approvals were synthetic; the current offline replay is not fresh agent onboarding or renewed approval.

The [eight-source experiment](runs/evidence-retention-20261014/REPORT.zh.md) recovered ORIC address components but omitted Companies types and changed APRA identifier subjects. CRICOS failed on merged cells; Aged Care changed whitespace in 10,423 native cells. The [pest/weed holdout](data/unseen-source/README.md) produced no validated config. Reliable generalisation remains unestablished. The current replay does not reverse those fresh-inference findings.

Each submitted profile has field confidence, provenance and selection reasons; none demonstrates an actual conflicting field. Withdrawing Companies affects 42 of the submitted 50 profiles and loses 164 field values. Membership stays fixed and identity review is flagged. Field confidence is separate from link confidence.

The original six-source inference used 87 provider calls, 1,563,821 tokens and US$0.2054033. This audit/replay added zero model calls. The latest author-reported project API estimate is US$3.10; an earlier archived US$6-7 self-report has unresolved scope differences, so no reconciled project total is claimed. The author estimated 7-10 cumulative active hours before this final closeout, exceeding the six-hour time box; closeout time was not separately logged.

## Infer, review and extract a new source

Supply native CSV/TSV, JSON or XLSX and original documentation in an evidence directory. After separately authorizing paid execution, set `OPENROUTER_API_KEY` through the environment:

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

`infer` and LLM-backed extraction make model calls. AST extraction does not. Inspect evidence, mappings and unresolved requirements before approving; changed evidence/config/ontology/runtime invalidates that approval. Requirements are caller-declared and frozen; a partial candidate retains its unresolved fields. Omit metadata when unavailable. Unknown paid outcomes stop without automatic resend.

OpenRouter defaults to `anthropic/claude-haiku-5.5`, low reasoning and an Anthropic provider allowlist. `--model` changes the model ID. The CLI default is `ccvibe`, requiring `FIRMABLE_MVP_API=1` and the existing `~/.claude/mvp-api/settings.json` profile; its `--max-budget-usd` is per call, not a run budget. OpenRouter rejects that option. Never commit credentials.

## Repository map

| Path | Contents |
|---|---|
| `firmable_runtime/`, `tests/`, `scripts/` | One runtime, offline checks and supporting commands |
| `data/catalog/`, `data/selection.json` | Catalogue, ranking, selection and review evidence |
| `data/assessment-inputs/` | Six original native inputs, documentation and licences |
| `outputs/` | Six configs, replayed observations, proposed links, queue, 50 profiles, withdrawal and human audit |
| `evidence/` | Current bindings, verification, frozen labels and necessary historical replay fixtures |
| `runs/` | Selected reports, metrics, safe receipts and historical review packets; scratch runs ignored |

Only necessary submission materials are tracked. Source licences remain separate from code; [attribution](data/assessment-inputs/ATTRIBUTION.md) is retained. The ontology contains an earlier explanatory note for `address.full` and is not claimed byte-identical to the original attachment. Internal drafts, full intermediate runs, credentials and caches are excluded.
