# Frozen new-source validation

The final runtime is `8e891fe9b5b31724e194ee5951cb20fe5d8a1fe848c7a9dae946a70266806f8f`. The original six-source submission remains a separately reproduced snapshot; none of the new sources or their labels is spliced into it.

## Outcomes

| Experiment | Result | Calls / tokens | Reported USD / online seconds |
|---|---|---|---|
| AEMO NEM XLSX, predecessor runtime `9aa20f50` | Failed before config: two business sheets unreadable and invalid selection was not recoverable | 6 / 66,005 | 0.0077097 / 21.869 |
| NDIS all-provider CSV, final runtime | Native preparation exceeded the unchanged 32 MiB bound; no model call | 0 / 0 | 0 / not model timed |
| NDIS SIL CSV, final runtime | Complete ABN task: 557 observations; 557/557 source/checksum agreement; raw records and locators unchanged | 8 / 131,746 | 0.0154342 / 41.181 |
| WEM participants/facilities CSVs, final runtime | Both tables readable, 298 records. Name requirement unresolved; no selected final candidate, approval or extraction | 8 / 145,393 | 0.0172829 / 42.643 |

Online times exclude operator preparation, downloads and post-run audits. Costs are provider-reported receipts, not invoice reconciliation. Each paid trial had a 40-call / 600-second new-send window / US$0.50 observed-cost guard, with US$0.25 in-flight reservation. This is not a provider-enforced billing ceiling. No trial hit those limits or had an unknown outcome.

## What the successful test establishes

The original [SIL native CSV](https://cdn.ndiscommission.gov.au/nqsc_website/sil_providers.csv) and [publisher documentation](https://www.ndiscommission.gov.au/provider-registration/find-registered-provider) were supplied with the ontology and no existing mapping. Native rows were not manually sliced or edited. Code and input hashes stayed fixed.

An original quotation was present but not bound to its field by the finite compiler. The existing generic host repair proposed binding the unchanged ABN rule to its original header; the model explicitly selected that candidate. Separate development validation passed, and generic AST extraction completed under labelled synthetic approval. All 557 ABNs agree with the explicit source column and an independent checksum. This is source agreement, not external owner or human ground truth.

The source contains no names or addresses. It establishes one minimum-usefulness task, not arbitrary-source success, name accuracy or relationship inference. The reading agent finished using supplied documentation, with no new remote excerpts; autonomous native-file acquisition was not demonstrated.

The first, larger NDIS export failed its native-size preflight. A recorded amendment added the separately selected smaller SIL resource before any model call. This departed from the initial one-resource selection plan; both outcomes are retained. The large download raised before storing its payload/hash, so only metadata, selection and the failure receipt remain. It was not a verified full native snapshot.

## What the complex test establishes

The [WEM registration resources](https://data.wa.aemo.com.au/) contain 121 participant records and 177 facility records. Original dictionaries distinguish a participant's business name, contact person and facility code. The model selected both tables and explained those role distinctions. It generated a postal-country/postcode candidate: a proposal-only probe emitted 12 values on six of eight rows, all independently source-consistent.

It did not establish a trading-name mapping and stopped rather than selecting its partial candidate, despite a host reminder. No independent development validation, final config, approval or full extraction followed. There was no name mapping, so the independent name-review model was not called. Reasoning/abstention does not establish emitted-subject accuracy.

The caller's pre-run `entity.trading_name` requirement assumed the generic business-name definition would support that role. This was an evaluation assumption, not confirmed trading-name truth; failure does not by itself prove false rejection. Direct locality/state/address opportunities also remained omitted, and the City's whole-address requirement was absent from the ontology. These are diagnostic discovery/finalisation gaps, not measured error rates. One stop explanation incorrectly described the partial candidate as having no useful values; saved execution evidence takes precedence.

WEM is a new exact resource from an already exposed publisher. Retained-history searches found no exact earlier resource trajectory, but publisher independence, deleted history and model pretraining are not ruled out. Full name/subject quality is N/A. This file has no ABN-owner pairs, explicit legal-name field, historical name branches or trustee relationships.

## Engineering verification and evidence

Generic reader changes preserve distinct physical columns, original whitespace and merged XLSX anchors without owner/name fill. Mixed-format files are all inventoried; invalid initial table choices get bounded content feedback. The final suite has 497 offline tests. Independent readable-table checks found zero cell differences on exposed AEMO (37,740 cells), CRICOS (971,008) and Aged Care (134,450), including 10,423 whitespace-only Aged Care values. Unsupported tables remain explicit gaps. Numeric XML-token audit corrections and the first failed audit are retained; these are physical-input checks, not semantic gains.

- [Final runtime and unchanged six-source outputs](../evidence/current-submission.json)
- [Engineering receipt](../evidence/native-contract-20261015.json)
- [Original AEMO failure receipt](../evidence/fresh-holdout-20261015.json) and [complete original trial archive](../evidence/fresh-holdout-20261015-trace.tar.gz)
- [SIL compact receipt](../evidence/fresh-native-holdout-20261015.json)
- [WEM compact receipt](../evidence/subject-capability-20261015.json)
- [Selected SIL/WEM audit materials](../evidence/onboarding-audit-20261015.tar.gz)

The selected audit archive contains frozen identities, selection/amendment records, provider receipts, safe decisions and configs, and result summaries. It excludes native rows, source pages and full model-context journals; those originals remain retained locally with hashes. It is not advertised as a complete public replay package. Public materials contain no API credential, and original source licences remain separate from code.
