# Schema inference and entity identification

One runtime agent generates mapping configs from native files, documentation and the ontology. The submission includes six agent-generated configs, their observations, proposed links, an unlinked queue, 50 profiles and review evidence. Frozen new-source tests show basic autonomous onboarding, with substantial limits on complex name and subject interpretation.

## Results and relationship design

Deterministic triage ranked 50 datasets from 1,290 retrieved catalogue records. A random 20-item human-confirmed, AI-assisted audit found 19 positives and one uncertain: a possible sample false-positive fraction of 0-5%, not a population estimate.

The current runtime reproduces 22,925 observations, 250 proposed entity groups, 479 cross-source pairs and 22,222 unlinked records from six unchanged configs. Typed identifiers anchor identity; names alone do not confirm links. Parent/subsidiary and brand/holder relationships remain unasserted without evidence.

The completed 50-link human audit has 44 correct, one incorrect and five uncertain: conservative precision 44/50 = 88%. The 0.99 threshold is an uncalibrated policy score. The sample covers 46 ABN anchors, not ACN-only precision or unlinked recall. Profiles remain proposed merges. They retain field confidence, provenance and selection reasons, but none demonstrates a real conflicting field. Withdrawing Companies affects 42 of 50 submitted profiles and removes 164 field values; membership stays fixed and identity review is flagged.

## 1. Cost and time

Historical Companies onboarding used 14 calls, 383,438 tokens, US$0.0524642 and 178.651 seconds, including acquisition; seven inference trace events are distinct from model calls. Original six-source receipts total 87 calls, 1,563,821 tokens and US$0.2054033.

New SIL onboarding used eight calls, 131,746 tokens, US$0.0154342 and 41.181 seconds. Its three planner decisions and eight inference trace events include proposing, probing, repairing, selecting and validating. Costs recur per source/config version. Full AST extraction makes zero per-record model calls; compute and human review costs were not measured.

My earlier project API estimate was approximately US$3.10. It is not reconciled with an archived US$6-7 estimate. The three latest paid tests add US$0.0404268 in provider-reported receipts; no reconciled project total is claimed. I estimated 7-10 cumulative active hours before final closeout, exceeding the six-hour time box. Subsequent audit and engineering follow-up time was not separately logged.

## 2. Agent design and checks

The agent inspects tables/documents, samples records, proposes mappings and expressions, probes execution, validates on initially withheld development rows, and revises or explicitly selects. Separate steps expose execution and evidence failures; checkpoints retain completed work. Unknown paid outcomes stop without automatic resend.

Human review exposes mappings, quotations, examples, omissions and unresolved requirements. Approval binds config, evidence, ontology and runtime. All formal experiment extractions used synthetic approval; no config has human approval. Link judgments are a separate human audit. Required fields are caller-declared; autonomous requirement discovery is unproven.

On SIL, an exact quotation initially failed field binding. An existing generic host repair rebound it to the original ABN header without changing the AST or deleting fields. The model explicitly selected the repaired candidate, which passed development validation. No operator wrote or edited the config.

## 3. Difficult sources and generalisation

ATO mixes business tables with workbook explanations; an earlier attempt included six explanatory rows. Its generic Name lacks registered-name evidence, leaving legal_name unresolved, as on three other original sources. Table scope, field discovery and subject interpretation remain the weakest steps.

A frozen AEMO workbook attempt failed before config generation because of reader/selection limits. Generic fixes now preserve duplicate columns, merged-cell anchors and whitespace, with bounded selection feedback. Independent physical-cell checks and 497 offline tests verify engineering behavior, not semantic improvement.

With the same repaired runtime frozen, a new 557-row SIL CSV completed ABN onboarding and extraction, with 557/557 source/checksum agreement. It has no names or addresses. The initially selected larger NDIS CSV exceeded the 32 MiB limit; the separate smaller resource was added by a recorded pre-call amendment, preserving the failure.

A new WEM two-table resource was readable, but did not finish onboarding: the model declined to equate a business-name definition with trading_name and stopped despite a partial postal-field candidate. Eight calls cost US$0.0172829. This does not prove a false rejection; name-role ground truth was insufficient. Broad generalisation and complex ownership remain unproven.

## 4. From six sources to 500

Reader bounds and useful-document acquisition block some sources first. Among readable sources, semantic review and human approval likely constrain throughput. Cache versioned evidence, partition by typed identifiers, and measure useful coverage, review time and cost per accepted source before increasing concurrency. No scale benchmark is claimed.

## 5. Changing the matcher

Retain immutable observations, native locators, evidence/config/model/ontology hashes, matcher versions, link reasons and human decisions. Distinguish proposed, accepted and rejected relationships so a new matcher can reconsider decisions without overwriting history.

Run old and new matchers on the same frozen observations, including previously unlinked and rejected records. Compare added/removed pairs, cluster splits/merges and affected profiles; review risky differences. Stage activation by bounded partitions and monitor false merges and review volume. Version memberships and derived profiles together, with explicit old/new entity-key mappings. Rollback must restore both. Existing withdrawal recomputation is narrower; matcher migration and rollback are not demonstrated.

## 6. With three more weeks

First improve evidence-bound name/subject discovery and partial-candidate finalisation, preserving supported branches. Investigate the incorrect/uncertain audited links. Evaluate omissions, false accepts/rejects, raw-value retention and cost on frozen cohorts and genuinely new holdouts. Then test matcher migration. A custom adapter is justified for a documented format gap when a reusable reader extension is uneconomical; isolate, test and disclose it rather than invent semantics.
