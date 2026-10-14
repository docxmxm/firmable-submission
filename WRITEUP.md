# Schema inference and entity identification

**Scope.** One runtime and one complete six-source repetition (pre-scope r2), with 22,925 observations. Companies retained 389/389 labelled current names; Business Names retained 888/888 labelled current business names. ATO, QBCC, Suitable Operators and ACT lack resolved legal-name mappings. Onboarding status is one complete and five partial. These are seen-source development results with synthetic config approvals. Current link review is 0/50; precision is unknown. Prior cohorts' reviews do not apply. [Evidence and bindings](evidence/index.json).

**Discovery.** Code retrieved 1,290 catalogue records and ranked 50. The retained 20-item, AI-assisted human confirmation found 19 yes and one uncertain: sample false-positive rate 0–5%. This is separate from link review. [Review evidence](data/catalog/human-review/CONFIRMATION-RECEIPT.json).

## 1. Cost

The selected six-source r2 onboarding used **67 model calls, 1,242,629 tokens and USD 0.1631553** in provider-reported receipts. Per-run process wall times sum to 494.32s; this is neither concurrent elapsed time nor engineering time. ACT alone used 11 calls, 196,258 tokens, USD 0.0249178 and 69.46s. All formal extraction used AST: zero per-record model calls. [Selected receipts](evidence/index.json).

Compute, preparation and review costs are unmeasured. Receipts are not invoices; complete historical project spend and six-hour compliance remain unverified. The [retained historical CLI ledger](evidence/historical-cli-costs.json) has a different scope and pricing basis. This r2 subtotal is not total development cost.

## 2. Agent design and weakest step

The runtime discovers native tables, separates proposal/development records, requests a mapping manifest and executable rules, probes candidates, independently reviews name roles, validates on initially withheld development records, and revises or explicitly selects. Distinct stages make execution failures and uncertain semantics visible. Requests, candidates, evidence hashes and checkpoints survive interruption; unknown provider outcomes stop without resend. A human sees mappings, examples, omissions, validation findings, remaining requirements and usage before approving the bound config.

The weakest step is mapping discovery and revision stability: technical validation does not establish useful recall. Companies status coverage varied 0/0/3 across repeats, while four sources' legal-name gaps persisted. The same version passed native scope/identity checks in 17/18 trials; ATO r1 included six explanatory rows. Selecting every readable table skips independent scope review in this baseline. r2 and r3 each retained all six native populations, but that does not establish reliable business completeness or unseen-source generalisation.

## 3. The hard one and custom code

ATO combines business and explanatory workbook tables. r2 retained 4,436 business rows; the name field's legal/trading role remains unresolved. I would first improve generic table-scope review and obtain original field definitions, then test without changing the evaluator's denominator. A name-like string or valid identifier alone does not prove its business role or owner.

Custom code is appropriate when a documented source requires an unsupported format or transformation and a reusable reader/DSL extension is not economical. I would preserve raw input, isolate the adapter, test it against original examples, disclose the exception and require human review. Missing semantic evidence calls for abstention or clarification, not a parser that invents certainty.

## 4. Scale

At 500 sources, source-document gaps and exception-review workload are likely to constrain onboarding first. Dense shared-identifier groups create pairwise matching work, and copied raw evidence increases storage. These are design risks inferred from the implementation, not a 500-source benchmark. I would measure reviewer time and model calls alongside CPU/storage, partition candidates by typed identifiers, store evidence references and keep deterministic extraction separate from onboarding. Per-record LLM extraction requires bounded extraction and verification calls and can dominate recurring costs.

## 5. Identity, profiles and changing the matcher

Qualified same-type identifiers define candidate groups with explicit cross-source pair evidence. Name, subject and membership conflicts can cause abstention. The fixed 0.99 score is a rule score, not measured precision. Shared identifiers do not establish parent/subsidiary or employer relationships, so those relationships are not asserted.

Profiles retain field confidence, provenance, losing values and selection reasons; absent facts remain absent. Field confidence is uncalibrated. The r2 rebuild produced 225 linked entities, 22,278 unlinked records and 50 profiles. Removing ASIC Companies affected 195/225 profiles, lost 729 fields, changed one field and dropped none. This recomputes fields under fixed membership; it does not re-prove identity. [Withdrawal result](outputs/withdrawal/withdrawal.json).

For a replacement matcher over millions of links, I would preserve raw locators, observations, source/config/ontology/runtime/model hashes, policies, thresholds and human decisions. Run old and new policies side by side; inspect pair additions/removals and cluster splits/merges; review risky deltas before activating bounded batches. Roll back memberships and profiles together. This is a migration proposal, not a demonstrated production migration.

## 6. Next three weeks

First finish human review of this cohort and inspect subject-ownership risks. Next make scope and supported-field retention measurable across repeated runs, then freeze the implementation and evaluate genuinely unseen sources. Prioritise generic fixes supported by those failures, followed by drift monitoring and bounded matcher migration. Packaging and offline tests alone do not close the mapping or evaluation gaps.
