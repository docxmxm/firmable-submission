# Schema inference and entity identification

One runtime agent generated six mapping configs for a generic extractor. Current runtime `9aa20f50` replays those unchanged configs against original native inputs, producing 22,925 observations, 250 proposed groups and 50 profiles. Config inference originated under `e3061c95`; replay is not fresh agent onboarding or renewed approval. The 225-group predecessor is preserved in Git. Generalisation remains unproven.

## 1. Cost and time

The author's latest API spend estimate is **US$3.10**. This is not a reconciled project total: an earlier archived self-report was US$6-7, with unresolved scope differences. Original six-source inference receipts total **87 calls and US$0.2054033**. Companies alone used **14 calls, 383,438 tokens, US$0.0524642 and 178.651 seconds**, including acquisition. Its seven trace events include two probes, two name reviews and development validation; events differ from model calls.

Inference is an onboarding cost per source/config version. AST extraction makes **zero per-record model calls**; CPU, storage and human review costs are separate. Before final closeout, I estimated **7-10 hours of cumulative active effort**: 3-4 on framework design, 3-4 on experiments, debugging and fixes, and 1-2 exploring generalisation. This updates an earlier under-six-hours active-time estimate and exceeds the six-hour time box; it is not elapsed calendar time. Final audit/replay effort was not separately timed.

## 2. Agent design and self correction

Deterministic catalogue filtering ranked 50 candidates from 1,290 records, avoiding model calls for obvious exclusions. A fixed random sample of 20 received human-confirmed, AI-assisted review: 19 contained identifiable businesses and one remained uncertain. The possible false-positive fraction within this sample is 0-5%, not a population estimate.

The agent inspects native tables and documents, samples records, proposes mappings and executable expressions, probes them, validates on initially withheld development records, and revises or selects a candidate. Separate steps expose execution and evidence failures while carrying state forward. For Companies, it revised the name proposal to use the companion Current Name field on historical-name rows. Unsupported fields remain unmapped with reasons. Replay restores 549 explicit address components; prior values and original records remain intact. Operator-declared required fields guided task acceptance; autonomous requirement discovery is unproven.

Human review exposes mappings, quotations, examples, omissions and unresolved requirements. Approval binds config, evidence, ontology and runtime. Original approvals were synthetic; offline replay inherits no renewed approval, and no config has human approval. Persisted requests, artifacts and checkpoints allow completed steps to be recovered. Failed document retrieval preserves local inputs; unknown paid outcomes stop without automatic resend.

The weakest step is table/subject interpretation and field discovery. In later eight-source runs, Companies type coverage fell from 386 to zero, while 693 APRA identifiers switched from trustee to fund. More emitted fields did not establish better semantic quality. Source claim time is also unresolved: all submitted observations use labelled ingestion or dataset-update proxies, excluded from recency comparisons.

## 3. The difficult source

ATO combines business tables and workbook explanations. The six-source output retains 4,436 business rows; an earlier attempt included six explanatory rows. Its generic Name field lacks registered-name evidence, so legal_name remains unmapped. I would require explicit table dispositions and subject definitions. A custom adapter is justified for a documented format gap when a reusable reader is uneconomical; it should be isolated, tested and disclosed, without inventing field semantics.

Later holdouts exposed reader limits: CRICOS failed on merged cells before inference; Aged Care generated address mappings but read single-space cells as empty strings in 10,423 cells across 5,377 rows. Address-component agreement does not make that end-to-end result acceptable.

## 4. Entity relationships and profiles

Qualified shared typed identifiers produce candidate pairs and entity groups. Subject/name conflicts and ambiguous identifiers cause abstention. The build has **479 cross-source pairs and 22,222 unlinked records**: 76 subject/ambiguity refusals and 22,146 without a qualified identifier or cross-source anchor. Names alone never confirm a link. Parent/subsidiary and brand/holder relationships are not identity; without source evidence, I leave them unasserted.

The fixed **0.99 threshold is an uncalibrated policy score**. The fixed sample now has **50 human judgments: 44 correct, one incorrect, five uncertain**. Conservative precision is **44/50 = 88%**, retaining uncertain cases in the denominator. Incorrect/uncertain proposals remain visible. The sample covers 46 ABN anchors, not ACN-only precision or unlinked recall. Historical AI judgments are excluded.

Profiles carry field confidence, provenance and selection reasons; missing values are omitted. Conflict retention is implemented, but none of the submitted 50 profiles has a conflicting field, so real-conflict display is not demonstrated. Removing Companies affects **42 of the submitted 50 profiles**, losing **164 field values**. Withdrawal retains membership and flags identity review. Profiles are proposed merges, not human-approved clusters.

## 5. Scale from six sources to 500

The human review queue likely constrains onboarding first: unreliable documents and ambiguous definitions consume calls and attention. Cache versioned evidence, partition candidates by typed identifiers, and measure review time, useful coverage and cost per accepted source. These are hypotheses, not benchmarks.

## 6. Replacing the matching model

Before changing millions of links, retain immutable observations, native locators, evidence/config/model/ontology hashes, matcher policy versions, link reasons and human decisions. Keep proposed, accepted and rejected relationships distinguishable, so a new matcher can reconsider decisions without overwriting history.

Run old and new matchers on the same frozen observations. Compare added/removed pairs and cluster splits/merges, trace their affected profiles, and review risky differences. Include previously rejected records to expose recall changes, rather than evaluating accepted links alone. Stage activation by bounded partitions, monitoring false merges and review volume.

Version memberships and derived profiles together. A rollback must restore both, with an explicit mapping between old and new entity keys. Existing withdrawal recomputation is narrower than this migration plan; deployment and rollback have not been demonstrated.

## 7. Next three weeks

Prioritise raw-value preservation and subject discovery; investigate audited incorrect/uncertain joins. Measure omissions, false accepts and review cost on fresh sources, then test matcher migration. Keep field and link confidence separate.
