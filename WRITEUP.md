# Schema inference and entity identification

**Result.** The final frozen runtime produced all 22,925 native observations across six development sources. Companies current names (389/389), Business Names trading names (888/888) and Suitable Operators types (8,184/8,184) agreed with narrow frozen labels; no labelled wrong values or misses occurred. Companies type coverage recovered to 389, without independent correctness labels. Two tasks completed and four retained unresolved legal-name requirements. The final 17-row Cooperatives holdout produced no validated config, so its accuracy is N/A. Generalisation and repeatability remain unproven. Approvals were synthetic, and link precision has no human measurement. [Results and evidence](RESULTS.md).

**Discovery.** Code retrieved 1,290 catalogue records and ranked 50. The retained 20-item, AI-assisted human confirmation found 19 yes and one uncertain: sample false-positive rate 0–5%. This historical catalogue assessment is separate from mapping, link review and the final holdout. [Confirmation](data/catalog/human-review/CONFIRMATION-RECEIPT.json).

## 1. Cost

The final six-source run used **66 calls, 1,153,833 reported tokens and USD 0.1580661** in 227.974 seconds. The failed holdout used six calls, 59,435 tokens and USD 0.0078227 in 37.565 seconds. This change's two complete validation attempts, including the earlier BN regression and successful NoWaste test, total **165 calls and USD 0.414728**. Formal six-source extraction used AST with no per-record model calls. [All attempts](evidence/validation-history.json).

These are provider receipts, not invoices. Preparation, engineering and review costs are unmeasured; historical project spending and compliance with the assignment's six-hour limit remain unverified. The historical CLI ledger and selected r2 costs have different scopes and are not substituted for total development cost.

## 2. Agent design and weakest step

The runtime discovers native tables, separates proposal/development records, requests a mapping manifest and executable rules, probes candidates, independently reviews name roles, validates on initially withheld records and lets the planner revise or select. Configs come from these runtime calls. Requests, original evidence, candidates, hashes and checkpoints survive interruption; unknown provider outcomes stop without resend. Approval binds the reviewed candidate, evidence, ontology and runtime.

The change improves what the planner can observe. Discovery distinguishes a field's independent meaning from its use as a dependency. Repair feedback compares pointer-resolved execution, target bindings, evidence declarations and repeated diagnostics, while preserving cached source lookups. It does not rewrite mappings or grant semantic approval. Anonymous regressions verify this feedback and recovery; the full suite passes 394 tests.

The weakest step remains evidence acquisition and semantic discovery. The final holdout had empty column descriptions but an official program link in its metadata. That link did not reach subsequent planning/mapping stages, and the available lookup tool only searched local evidence. The model omitted the name; the host's limited entity-status evidence rule rejected its proposed status mapping. The existing separate acquisition workflow was not invoked. A useful next harness change is to connect that source-link and acquisition path, preserving original citations before inference, then evaluate independently. Adding source aliases would not address the information-flow gap.

Six-source recovery is encouraging but not causal proof: BN passed its first candidate, while Companies made one substantive repair. Neither demonstrates escape from repeated cosmetic revision. The earlier attempt lost all 888 BN names; its successful NoWaste result belongs only to that earlier runtime and remains archived alongside the failure.

## 3. The hard one and custom code

ATO combines business and explanatory workbook tables. The final run retained 4,436 business rows but still lacks a resolved legal-name mapping. Historical ATO r1 included six explanatory rows. Selecting all readable tables still skips independent scope review, so native readability alone is insufficient business-scope evidence.

Custom code is appropriate for a documented unsupported format or transformation when a reusable reader/DSL extension is not economical. I would preserve raw input, isolate and test the adapter, disclose the exception and require review. Missing semantic context should first trigger source-evidence acquisition or an explicit unresolved finding, not a source-specific parser that invents certainty.

## 4. Scale

At 500 sources, missing documentation and exception-review workload are likely to constrain onboarding first. Dense identifier groups create pairwise work, and copied raw evidence increases storage. These are implementation risks, not a 500-source benchmark. Measure reviewer time, calls and recall alongside CPU/storage; partition candidates by typed identifiers and keep deterministic extraction separate from onboarding. LLM extraction and verification calls can dominate recurring costs.

## 5. Identity, profiles and replacing the matcher

Qualified same-type identifiers define candidate groups with pair evidence. Name, subject and membership conflicts can cause abstention. Explicit-person/unknown-subject pairs now remain unresolved when ownership is unclear; losing a counterpart type should not create assent. In the final run, all 29 previously identified risk records remained unlinked, this time through the existing person/company conflict rule because Companies types recovered. These are review cases, not confirmed false links. The fixed 0.99 score is uncalibrated.

The final build produced 225 linked groups, 22,278 unlinked records and 50 profiles. Profiles retain provenance, confidence, losing claims and selection reasons. Confidence is uncalibrated. The separate historical r2 withdrawal test affected 195/225 profiles when Companies was removed, losing 729 fields and changing one. It recomputed fields under fixed membership; it did not re-prove identity. [Historical withdrawal](outputs/withdrawal/withdrawal.json).

For a new matcher over millions of links, preserve raw locators, observations, source/config/ontology/runtime/model hashes, policies and human decisions. Compare pair changes and cluster splits/merges, review risky deltas, activate bounded batches and roll back memberships with profiles. This is a migration proposal, not a demonstrated deployment.

## 6. Next three weeks

Connect the existing source-evidence acquisition path to onboarding and preserve original metadata links through planning; validate on new sources without hand-written schemas. Measure field retention over repeated fixed-input runs and report failures with their original denominators. Complete human review of the current linked and abstained populations before making precision claims. Then measure documentation drift, review workload and bounded matcher migration. More validation gates alone do not supply missing evidence or establish useful recall.
