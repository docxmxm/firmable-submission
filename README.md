# Firmable schema inference

The runtime includes a multi-step `infer` agent that starts from native data, documentation and the ontology, then proposes mappings, probes execution, validates and revises. [Saved Companies onboarding](runs/six-source/real-asic-companies/onboard.json) demonstrates an earlier real-model trajectory. This is separate from executing saved configs.

The final runtime is `8e891fe9`. It reproduces the six submitted observation files and all downstream artifacts byte-for-byte from six unchanged agent-generated configs: **22,925 observations, 250 proposed entity groups, 479 cross-source pairs, 22,222 unlinked records and 50 profiles**. Offline replay is separate from fresh inference; historical config approvals are not renewed.

A frozen new **557-row SIL CSV** completed autonomous ABN onboarding, development validation and generic AST extraction, with **557/557 original-value/checksum agreement** and no operator-written mapping. An existing bounded host evidence repair supplied a candidate that the model explicitly selected. This source has no names or addresses, and the experiment approval was synthetic.

A separate new **298-row WEM resource** was readable, but onboarding remained partial: business-name role evidence was insufficient and the model stopped without finalising its useful partial postal-field candidate. Broad name/subject generalisation remains unproven. The first large NDIS resource also exceeded the 32 MiB input bound; its failure and the pre-call smaller-resource amendment remain disclosed. See [new-source validation and audit materials](docs/NEW-SOURCE-VALIDATION.md).

An earlier AEMO workbook attempt at runtime `9aa20f50` failed before config generation. Its [receipt](evidence/fresh-holdout-20261015.json) and [original trial archive](evidence/fresh-holdout-20261015-trace.tar.gz) remain separate. Generic reader and initial-selection recovery fixes now pass **497 offline tests**, with independent physical-cell checks on three exposed regression sources. This verifies engineering behavior, not improved semantic accuracy. See the [engineering receipt](evidence/native-contract-20261015.json).

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

These commands make no model calls. The final package is checked from a temporary clean checkout with no pre-existing virtual environment: **497 tests**, exact native replay, exact downstream rebuild and complete human-audit validation. See [submission verification](evidence/submission-verification.json) for measured timing and content hashes. The host dependency cache was available; installation time elsewhere depends on package access. Offline reproducibility does not establish mapping accuracy.

## Version and audit lineage

The configs were inferred under runtime `e3061c95`. Final runtime `8e891fe9` replays them without editing mappings or native rows. The six-source outputs and human-review packet are byte-identical to the prior `9aa20f50` snapshot; [current bindings](evidence/current-submission.json) record that revalidation. Five source outputs are byte-identical to that earlier run. For Suitable Operators, the current generic address parser adds three original-value components on 183 records (549 values); no previous canonical values, raw records, locators, identifiers or snapshot ingestion times changed. Component agreement does not establish identifier ownership or address semantics.

All 479 pair contracts and the 100 sampled observation objects are unchanged from the repaired-policy review packet. Its 18 human judgments were preserved, and the reviewer completed the remaining 32 in an interface with blank decisions and no AI prefill. The separate historical mixed AI/human audit is excluded from the current human precision. This sample has 46 distinct ABN anchors; it does not measure ACN-only precision or unlinked-record recall.

The previous 225-group submission remains recoverable at Git commit `f673ff961b8cceb7afb3cf2fdeaa96998d0ed46e`. Historical acquisition receipts, [six-source snapshot](evidence/latest-six-source.json), [validation history](evidence/validation-history.json) and [evaluation archive](evidence/evaluation-traces.tar.gz) retain their original runtime and artifact identities. Paths in those historical receipts describe that revision, not the current `outputs/`. Earlier r2 artifacts remain under `historical-r2/outputs/` in the archive, with their [index](evidence/index.json) and [trace archive](evidence/six-source-r2.tar.zst). Moving old journals does not authorize resuming paid calls.

## Results and limitations

The catalogue contains 1,290 retrieved records, a programmatically ranked shortlist of 50, and a fixed random 20-item human-confirmed, AI-assisted audit (19 positive, one uncertain). The possible sample false-positive fraction is 0-5%, not a population estimate.

Frozen narrow labels retain Companies names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184). These are source-agreement checks, not human gold or whole-system accuracy. Legal names remain unresolved for ATO, QBCC, Suitable Operators and ACT. All config approvals were synthetic; the current offline replay is not fresh agent onboarding or renewed approval.

The [eight-source experiment](runs/evidence-retention-20261014/REPORT.zh.md) recovered ORIC address components but omitted Companies types and changed APRA identifier subjects. That historical runtime failed on CRICOS merged cells and changed whitespace in 10,423 Aged Care native cells. The current reader repairs those physical-input defects in exposed regression checks; no fresh semantic improvement on those sources is claimed. The [pest/weed holdout](data/unseen-source/README.md) produced no validated config. The new SIL result establishes narrow ABN-only onboarding; broad generalisation remains unestablished, and the current replay does not reverse those historical fresh-inference findings.

Each submitted profile has field confidence, provenance and selection reasons; none demonstrates an actual conflicting field. Withdrawing Companies affects 42 of the submitted 50 profiles and loses 164 field values. Membership stays fixed and identity review is flagged. Field confidence is separate from link confidence.

The original six-source inference used 87 provider calls, 1,563,821 tokens and US$0.2054033. This audit/replay added zero model calls. The author-reported project API estimate before the AEMO follow-up was US$3.10; an earlier archived US$6-7 self-report has unresolved scope differences, so no reconciled project total is claimed. The author estimated 7-10 cumulative active hours before this final closeout, exceeding the six-hour time box; closeout/follow-up active time was not separately logged. The AEMO, SIL and WEM follow-ups add US$0.0404268 in provider-reported model costs; preparation and human-review costs are excluded.

## Infer, review and extract a new source

Supported native inputs are **CSV, TSV, JSON and XLSX**. New-source evaluations are restricted to these formats. Put original native files and documentation in an evidence directory; keep catalogue metadata outside it and pass `--metadata` separately.

- CSV/TSV: UTF-8 or BOM-marked UTF-16, quoted cells and multiline values. Header selection is a reviewable text-row heuristic. Duplicate/blank headers and extra unlabelled columns retain separate physical column IDs and original header/position metadata; generated IDs are not semantic evidence. Whitespace and leading zeroes are preserved.
- JSON: nonempty arrays of objects, at the root or under nested object keys. Separate record arrays are inventoried; nested objects/arrays, nulls and missing keys remain in the raw records.
- XLSX: multiple worksheets and physical cell positions. Merged ranges are retained without filling names or owners into other cells. Cached formula values are read with a limitation; formulas without caches and Excel error cells remain explicit reader gaps. Excel is not recalculated.

All supported native files in the directory are inventoried. Ambiguous separated table regions remain explicit unreadable tables, while other readable worksheets can proceed. Initial invalid table selections receive at most two content corrections; unknown model outcomes stop without resend. Input bounds remain 32 MiB per file, 64 MiB expanded XLSX, 100,000 rows per sheet/record array, and one million XLSX cells per workbook. Uncertain meaning or ownership still requires abstention and human review.

For a new paid inference run, supply `OPENROUTER_API_KEY` locally without committing it. Declare required fields only when the source can support their roles; unsupported requirements remain partial:

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
