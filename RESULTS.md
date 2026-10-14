# Results and validation

## Current submission - 15 October 2026

Runtime `9aa20f50` now reproduces the submitted observations and all downstream artifacts. It replays six unchanged model-generated configs from the original `e3061c95` inference, using original native snapshots and inherited snapshot ingestion times. No model calls, mapping edits, native-row edits or renewed approvals were introduced. [Current hashes and lineage](evidence/current-submission.json).

| Active output | Count |
|---|---:|
| Observations | 22,925 |
| Proposed entity groups | 250 |
| Cross-source proposed pairs | 479 |
| Unlinked records | 22,222 |
| Submitted profiles / field values | 50 / 252 |
| Human audit | 44 correct, 1 incorrect, 5 uncertain |
| Conservative precision at policy threshold 0.99 | 44/50 = 88% |

The five uncertain cases remain in the denominator. The single incorrect case remains visible in the proposal population; it was not dropped after sampling. The policy score is not calibrated. This is a fixed ABN-anchored sample (46 distinct anchors), with no ACN-only or unlinked-recall estimate. Profiles remain proposed merges, not human-approved identity clusters. [Human judgments](outputs/human-review/reviews.csv), [score](outputs/human-review/summary.json), [authorship and rebinding receipt](outputs/human-review/CONFIRMATION-RECEIPT.json).

Five replayed source files are byte-identical to their original outputs. Suitable Operators gains locality/state/postcode on 183 records (549 values), each checked against explicit original location components; all earlier values and all raw records, locators, identifiers and timestamps remain unchanged. This establishes narrow value agreement, not new semantic gold. The 479 pair contracts and all 100 sampled observation objects are unchanged, allowing the original 18 human entries plus 32 new human judgments to be bound explicitly to the active population. The historical mixed AI audit is excluded.

Withdrawal of Companies affects 220 of the 250 population profiles and loses 831 fields. Among the submitted 50, 42 are affected and 164 field values are lost. Membership stays fixed and identity review is flagged. The 50 profiles contain no conflicting field example.

The prior 225-group submission and its empty human audit remain in Git commit `f673ff961b8cceb7afb3cf2fdeaa96998d0ed46e`. **Everything below describes the historical acquisition-connected experiment at that revision.** Its costs, approvals, field labels and failures remain unchanged; paths in historical receipts refer to that revision. Current replay does not establish better fresh-agent generalisation or reverse the later eight-source/holdout failures.

## Historical real-model validation - 14 October 2026

**Source-link acquisition is connected, but this run did not obtain new usable evidence or establish better generalisation.** The frozen runtime retained all 22,925 six-source observations: two complete tasks and four partial tasks. Labelled fields were retained; unlabelled Companies type coverage fell from 389 to 386. The new 149-row pest/weed source produced no validated config or formal observations; accuracy is **N/A**.

The [six-source snapshot](evidence/latest-six-source.json), [holdout snapshot](evidence/latest-unseen-source.json), [validation history](evidence/validation-history.json) and [full trace archive](evidence/evaluation-traces.tar.gz) retain successes and failures. At the preserved revision, `outputs/` contains that complete historical cohort. Old r2 outputs remain under archive prefix `historical-r2/outputs/`. Current traces use `acquisition-connected/`; earlier attempts retain their original prefixes, including the historical prefix `current/`.

## Harness change

Original metadata/document URLs now reach the reading agent with exact source bindings. Before inference, the existing acquisition workflow lets it fetch, search, read or finish. It may finish without fetching when supplied documentation is sufficient. Only original captured excerpts can enter the frozen inference evidence; URLs and model explanations cannot become field definitions. The existing inference, revision, review, approval and extraction lifecycle then uses that evidence.

Metadata links may point to another host; subsequent page references retain the existing origin restriction. Same-host HTTP-to-HTTPS upgrades are supported. Captures retain hashes and receipts, resume does not resend unknown outcomes, and original native bytes/locators remain unchanged. Acquisition is a bounded phase before inference, not a fresh fetch cycle after every failed candidate. Static-text capture does not support PDF/XLSX downloads or browser rendering.

Configs came from runtime model calls. No source-specific branch, hand-written mapping, native-row edit, ontology change within this experiment or label injection was added. The retained ontology includes an earlier explanatory note for `address.full`, as disclosed in README. **407 offline tests**, static checks and independent code review passed. New scripted integration tests exercise original quote → evidence snapshot → candidate revision → approval/extraction, plus optional no-fetch, fetch failure and interruption recovery. These are behavior tests, not model-quality evidence.

## Six-source result

Runtime: `e3061c95bd1865e826e92bc2bdf0a9bfac5017090c547e527341e0f7f4517d98`. Each source ran once with `anthropic/claude-haiku-5.5`, low reasoning, Anthropic routing, non-streaming transport and three concurrent workers. Runtime, runner, original inputs, ontology and frozen labels remained unchanged.

Counts are informative emitted values, excluding `unknown`. Unlabelled counts are coverage, not accuracy.

| Source | Native rows / observations | ABN | ACN | Current name | Entity type | Address |
|---|---:|---:|---:|---:|---:|---|
| ASIC Companies | 389 / 389 | 389 | 389 | 389 legal | 386 | 0 |
| ASIC Business Names | 1,000 / 1,000 | 802 | 0 | 888 trading | 0 | 0 |
| ATO Corporate Tax | 4,436 / 4,436 | 4,400 | 0 | 0 | 0 | 0 |
| QBCC | 1,000 / 1,000 | 623 | 326 | 0 | 0 | 1,000 full source addresses |
| Suitable Operators | 15,100 / 15,100 | 299 | 7,901 | 0 | 8,184 | 14,825 each: locality, state, postcode |
| ACT historical contracts | 1,000 / 1,000 | 759 | 0 | 0 | 0 | 0 |

Companies also emitted 389 registration dates and three informative statuses. All six passed native population, identity and raw-value guards. Companies and Business Names completed their declared tasks; four other sources retained unresolved legal-name requirements.

Narrow source-agreement labels report **389/389 Companies names, 888/888 BN trading names and 8,184/8,184 SOR types correct**, with zero labelled wrong values or misses. SOR also passed 6,714 type-abstention and 15,100 legal-name-abstention checks. These are not human gold or global accuracy. Compared with the preceding cohort, the only coverage difference was Companies type **389 → 386**: three `Type=FNOS` rows lacked type output. Their names remained correct; type correctness is unlabelled. This small fluctuation is accepted for the current submission scope; it does not trigger another repair/retest cycle. Statistical repeatability has not been measured.

## Actual evidence acquisition

| Source | Acquisition calls | Fetch outcome | Exported excerpts |
|---|---:|---|---:|
| Companies | 1 | Finished; existing docs judged sufficient | 0 |
| Business Names | 2 | Searched, then finished; existing docs judged sufficient | 0 |
| QBCC | 1 | Finished; existing docs judged sufficient | 0 |
| ATO | 5 | Catalogue fetched; searches found no relevant excerpt | 0 |
| Suitable Operators | 2 | XLSX resource rejected by static-text capture limits | 0 |
| ACT contracts | 7 | Catalogue fetched; subsequent JSON request failed certificate verification | 0 |
| Pest/weed holdout | 3 | Metadata failed certificate verification; program URL returned 404 | 0 |

Across seven sources: six fetch attempts, two successful captures, four failures and zero explicit read actions. Searches returned no exportable excerpts. Acquisition used **21 calls, 210,313 tokens and USD 0.0250545**, already included in total usage. All inference requests retained their original evidence directories because no supplement was available. The original documentation and acquisition policy bindings were independently checked in actual model requests.

This demonstrates model-directed navigation, fetch attempts, failure feedback and optional stopping. It does **not** demonstrate new fetched evidence improving a mapping or revision. Acquisition receipts report `partial` when no excerpt was exported, even for intentional finish; that status is separate from task completion. TLS verification remained enabled, and failed fetches were not bypassed with manually supplied pages.

## New development holdout

**List of pest and weed control operators in the ACT** (`gg97-ghvs`), ACT Government, CC BY 4.0, was selected before native inspection. Runtime froze at 06:31:22 UTC; the full 149-row CSV and original metadata were downloaded afterward. No match was found in retained catalogue/native development records. This is a new dataset at a previously used publisher, not proof about a new publisher, deleted history or model pretraining. [Input and provenance](data/unseen-source/README.md).

The request supplied unchanged native data, original metadata, the ontology and no required fields. Column descriptions were empty. Independent labels were frozen before calls and never entered inference. They contain **zero positive mapping opportunities**: `Business name` was not established as legal or trading name. Name/type output could only be measured as coverage; 11 unsupported fields had separate abstention opportunities. This source cannot establish positive accuracy even if output exists.

The agent tried the original metadata URL (certificate verification failed), then the original program URL (HTTP 404), using the first failure in its next decision. Inference selected the sole 149-row table and stopped: no mapping candidate, probe or revision. There is no approved config, formal output or evaluated abstention success. Metrics and coverage remain **N/A**, with the original population retained. The source was not replaced and its schema was not manually filled.

Earlier NoWaste success and Cooperatives failure remain archived against their own runtimes. Neither validates this runtime. Current gaps concern acquisition reliability, useful-document discovery and unavailable field semantics; extra acceptance gates do not supply evidence.

## Linking, approval and cost

Current onboarding usage, including acquisition and validation calls:

| Source | Calls | Tokens | Reported USD | Seconds |
|---|---:|---:|---:|---:|
| Companies | 14 | 383,438 | 0.0524642 | 178.651 |
| Business Names | 15 | 313,028 | 0.0401336 | 127.422 |
| ATO | 18 | 250,173 | 0.0301525 | 103.214 |
| QBCC | 13 | 239,294 | 0.0316754 | 102.157 |
| Suitable Operators | 13 | 205,138 | 0.0293070 | 126.272 |
| ACT contracts | 14 | 172,750 | 0.0216706 | 81.910 |

One model call is not necessarily one logical agent step. Full action sequences are retained in the traces; formal AST extraction made no per-record API calls.

Current downstream output: **225 linked groups, 22,278 unlinked records and 50 profiles**. All 29 historical risk pairs were rejected by `person_company_subject_conflict`; all 29 distinct involved records remained unlinked. Independent checks verified bindings and unchanged group memberships from r2. Person/unknown protection remains covered by earlier replay and anonymous regressions; these live pairs exercise person/company protection. They are review cases, not confirmed false links.

Six configs received explicitly synthetic benchmark approvals. There were zero human-approved configs and zero human link reviews; precision is unknown and the fixed 0.99 policy score is uncalibrated. The holdout had no approval.

Withdrawal was recomputed from the current observations. Removing Companies affected 195 of all 225 profiles and lost 730 fields, with zero changed values or deleted profiles. Among the submitted 50 profiles, 44 were affected and 166 fields were lost. Membership is held fixed and identity review is flagged. The current 50 profiles have no observed conflicting values; the submission does not manufacture a conflict example.

| Retained evaluation | Calls | Reported tokens | Reported USD | Wall seconds |
|---|---:|---:|---:|---:|
| Pre-change six-source reference | 75 | 1,427,814 | 0.1914494 | 220.761 |
| Earlier repair: six sources, BN regression | 83 | 1,747,302 | 0.2279006 | 252.298 |
| Earlier repair: NoWaste holdout | 10 | 155,634 | 0.0209386 | 67.404 |
| Preceding runtime: six sources | 66 | 1,153,833 | 0.1580661 | 227.974 |
| Preceding runtime: Cooperatives failure | 6 | 59,435 | 0.0078227 | 37.565 |
| Current acquisition change: six sources | 87 | 1,563,821 | 0.2054033 | 262.234 |
| Current acquisition change: pest/weed failure | 5 | 51,643 | 0.0063431 | 27.981 |

**This change used 92 calls, 1,615,464 reported tokens and USD 0.2117464.** All rows above total 332 calls and USD 0.8179238. Current model receipts have known successful transport outcomes and reported costs; application/document-fetch failures still count. These are provider receipts, not invoices or total historical spending. Execution time does not measure engineering effort or establish the assignment's six-hour limit.

## Reproduction

After paid execution is authorised, run without an existing corresponding run directory. The runner accepts a secret environment variable or hidden prompt:

```sh
uv sync --frozen
uv run python -m firmable_runtime check
uv run python scripts/run_six_sources.py
uv run python scripts/score_six_sources.py runs/six-source
uv run python scripts/run_six_sources.py --request data/unseen-source/request.json
uv run python scripts/score_six_sources.py runs/unseen-source \
  --labels evidence/unseen-source-labels.json --evidence-root data/unseen-source/evidence
```

Do not overwrite or resume completed evaluations under changed code. The runner refuses existing directories and does not automatically resend unknown outcomes. Its USD 2 observed-cost guard is a local soft limit, not a billing cap. Network outcomes may differ by machine/date.

Full journals remain in two fixed local `runs/` directories and in the single committed archive with a per-file hash manifest. Compact snapshots expose scores, configs, acquisition receipts, usage and bindings. Historical absolute trace paths are provenance, not portable resume instructions. There is one active runtime, with no source-wise best-run selection.
