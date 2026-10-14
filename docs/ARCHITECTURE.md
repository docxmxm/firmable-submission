# Runtime architecture and contracts

This document describes the **saved cohort runtime** (`e3061c95bd1865e826e92bc2bdf0a9bfac5017090c547e527341e0f7f4517d98`), recorded in [generic validation](../evidence/generic-validation.json). The current code is the later experimental runtime `9aa20f50`; the branch/evidence-retention extensions below describe its additions. Saved outputs have not been regenerated or reapproved under that runtime. It connects model-directed source-evidence acquisition to inference, while retaining discovery/repair context and subject-risk handling. The saved six-source r2 cohort retains its original pre-scope runtime identity (`bec320c33a982dcfd5e31f9e0c8c74d68401ea0855643a07028855adbf5e40de`) and historical scope in the [evidence index](../evidence/index.json).

## One source through the system

1. `workflow.infer_source` saves the original request and locks the run directory. It mechanically locates URLs in original metadata/document pages, retaining their hashes, JSON pointers or page offsets. When links exist, a separate reading journal lets the agent choose fetch/search/read/inventory or finish without fetching. Only exact read excerpts from captured original pages enter a frozen supplement; navigation and model explanations do not. Original native bytes and relative locators are preserved. The existing inference loop then discovers readable tables, raw values and documentation from that snapshot.
2. The model selects tables. If any readable table is omitted, a separate scope review must provide a complete include/exclude partition and exact source quotations. **Selecting every readable table skips that extra review.** It can therefore include explanatory tables; ATO repetition 1 demonstrated this limitation.
3. The host separates proposal records from development validation records and freezes caller requirements. The bounded planner can inspect field evidence, propose candidates, probe them, revise them and explicitly select one.
4. Candidate generation uses separate control, mapping-manifest and AST-rule or LLM-task calls. The first AST manifest gets one independent discovery review. Its saved request distinguishes each mapping's primary field from dependencies and lists fields used only as dependencies so their independent meanings remain discovery opportunities. A documented code-to-meaning relation can support a directly equivalent canonical enum; unexplained codes and ambiguous categories remain unresolved. The host assembles the config; proposed mappings still need execution and semantic checks. This context does not require a new target or prove complete field discovery.
5. AST probes compile and execute the config. Independent name review checks original quotations, branches, current/historical roles and same-target companion mappings. A supported, bound review can be rechecked and supplied to the AST compiler for unresolved name semantics. The reviewer remains a fallible model.
6. Failed candidates produce field-bound feedback. The planner receives `repair_progress`, derived from immutable candidates and lookup receipts: actual pointer-resolved AST/target/time/subject changes, field-binding changes, evidence-declaration changes, repeated structured diagnostics, and new versus cached lookups. Rewording descriptions or an omit reason does not count as changing execution. This observation neither changes semantic review nor chooses a candidate; reviewer suggestions still need original-source verification. A bounded removal-only repair can propose a supported subset or remove a rejected enum entry where abstention is preserved. Eligible AST alternatives can be compared on the same development records before selection. The planner must explicitly select; field counts do not grant approval.
7. Development validation produces the final candidate and task acceptance. `review_source` exposes mappings, examples, omissions, findings, unresolved requirements, alternatives and usage. `approve_source` binds the reviewed evidence. `extract_source` checks that binding before formal extraction.
8. `build_outputs` links qualified typed identifiers, records abstentions, and builds profiles with field provenance. An explicit person paired with an unknown subject remains unlinked for review, even with a shared identifier. `withdraw_outputs` recomputes fields for existing entity keys and flags identity review.

`acquire_source_evidence` remains available separately and is now invoked by `infer_source` when original navigation links exist. The reading agent sees native structure, original documentation, metadata and the ontology; it can stop immediately when no further reading is useful. Metadata/navigation links can lead to another host because the exact link is bound to the supplied source. Subsequent page references stay within that origin, with same-host HTTP-to-HTTPS upgrades allowed. Only supported static text types are captured; PDF downloads, executed JavaScript and cross-host redirects remain unsupported. A failed fetch is recorded; the unchanged local evidence can still be used. Acquisition completion reports reading workflow status, not semantic certainty.

## Ownership

| Module | Responsibility |
|---|---|
| `cli`, `workflow` | Public commands, process lock, review/approval and extraction lifecycle |
| `agent`, `acceptance`, `branch_repair` | Bounded planning, candidate memory, local repair proposals, development checks and immutable caller requirements |
| `candidate_assembly` | Separate manifest/rule/task calls and deterministic config assembly |
| `field_evidence`, `source_document_tools`, `config_audit` | Original document lookup and bound independent name review |
| `acquisition`, `source_acquisition` | Model-directed source-document reading, capture, citations and durable tool results |
| `runtime`, `store` | Identity, durable requests, artifacts, checkpoints and usage |
| `provider`, `openrouter`, `openrouter_wire` | ccvibe/OpenRouter transport, strict response formats and safe receipts |
| `native`, `schema` | Native records/locators and config/response validation |
| `execution`, `engine`, `semantic_support`, `llm_route` | One execution interface with AST and LLM backends; type, evidence and subject checks |
| `identity` | Identifier linking, abstention, field selection and withdrawal impact |
| `common` | Strict JSON, hashing, path checks and atomic writes |

## Payloads and acceptance

The subsequent branch-retention repair extends the existing one-attempt subset path. A name review must be rebound to the original config and documentation, with quotations, branch declarations and companion coverage rechecked. Only a direct `when` output arm with no documented current-value obligation can be omitted while supported siblings and predicates remain unchanged. Primary field bindings, shared transforms and value-fallback wrappers are not rewritten. Unattributable failures retain the existing whole-group fallback. Every proposal needs a new probe, independent name review, explicit selection and development validation; it inherits no approval. Selection also compares isolated values on exposed proposal rows, so retaining a target while losing supported enum values can trigger the same bounded repair. This does not prove recall on unsampled branches or reviewer correctness.

The evidence-retention extension uses that same one-attempt path to try an exact original primary header for a failed evidence reference. The unchanged compiler must accept the isolated rule and the complete resulting config; AST, native field identity and semantic constraints remain unchanged. Finalization rejects an AST candidate when a same-scope, development-validated peer contains its exact mapping contracts, adds only different targets, and loses or conflicts with none of its observed values. It requires an explicit new selection, revision or stop; it never merges candidates or selects by field count. Different expressions, evidence, table scope, time/subject context, conflicting values, unobserved extras and unvalidated peers do not establish this obligation. Repeated refusal remains bounded and can end without a finalized config; unknown paid outcomes are never resent.

**Inference request:** source ID, evidence and run directories, ontology path, optional catalogue metadata and `required_fields`. The Python API also accepts bounded development settings. Source bytes, metadata, ontology, runtime, model and policy settings bind run identity.

**Config:** selected native tables, field mappings, canonical targets, transformations, mapping confidence, semantic evidence and executable pointers. AST configs contain bounded expressions; LLM configs contain bound task instructions. Unmapped fields retain reasons. A model-written validation flag cannot authorize execution or ingestion.

**Observation:** raw record, source ID/locator, canonical entity/address values, provenance, extractor version, time and time basis, source reliability and field confidence. Missing/null/blank native values remain distinct. AST extraction makes no per-record model calls. LLM extraction batches at most four records, with an independent verification call for batches that contain host-valid claims.

**Task acceptance:** a required field needs a validated mapping and accepted informative values. Deleting a mapping does not delete its requirement. This checks minimum usefulness, not complete recall or semantic accuracy. A technically validated partial candidate can be reviewed and explicitly approved with its gaps visible; a failed candidate cannot. Formal extraction reports execution and task status separately and retains unresolved findings.

**Links and profiles:** the frozen matcher accepts only threshold `0.99`; this fixed policy score is not a calibrated identity probability. Person/unknown pairs retain their subject evidence and `person_unknown_subject_needs_review` reason for review. Both-unknown pairs retain the existing shared-identifier policy. These checks do not establish complete identifier-owner evidence; identifying a licensed person, for example, does not prove ownership of an ABN in that row. Profiles choose values using source reliability, field confidence, comparable source-stated time and disclosed deterministic ties. Losing claims and provenance remain visible. Withdrawal does not re-prove entity membership. Historical field-confidence values and a lack of observed conflicts do not validate conflict resolution on new data.

The subsequent offline identity repair uses policy `common-anchor-0.4-name-roles`: registered names retain their own conflict checks, while distinct or multiple trading names neither create nor conceal a registered-name conflict. Identifiers, subject checks and the threshold remain required; aliases never supply an identity anchor. Multi-anchor ambiguity rejection also clears the diagnostic policy score. The [entity link audit](ENTITY-LINK-AUDIT.zh.md) distinguishes scoped replay proposals from the unchanged saved cohort and its pending human review.

## Recovery

A linked-source run also stores `input-request.json`, `source-links.json`, a separate `acquisition/` journal and `capture/` originals. When excerpts exist, `evidence/` holds native input copies and the original-source supplement. Final `request.json` binds this effective snapshot and acquisition receipt; review rechecks captured bytes and the export. The usual run stores `request.json`, `checkpoint.internal.json`, immutable `artifacts/`, `trace.json`, `onboard.json` and, after approval, `approval.json`. Formal extraction has a separate child journal.

```text
prepared → sent → completed
                → known_failed
                → model_outcome_unknown → stop; no blind resend
```

Completed calls and execution results are reused only with matching bindings. Known format failures have bounded correction paths. One process owns a run directory. Changed evidence, configuration, ontology or runtime invalidates review/resume boundaries; create a new run when those inputs change.

## Evidence limits

The saved runtime passed 407 offline tests; the later experimental runtime passed 478. Current checkout checks are recorded in [submission verification](../evidence/submission-verification.json). Scripted discovery tests establish request context, assembly and rejection boundaries, not improved real-model discovery. Link abstentions identify review cases, not confirmed false links. Current run results are reported separately in [RESULTS.md](../RESULTS.md); historical r2 results below do not validate the changed runtime.

The retained pre-scope evaluation passed native scope/identity checks on 17/18 source trajectories; repetitions 2 and 3 each had six complete native outputs. ATO repetition 1 included six explanatory rows. Legal names remained unresolved for ATO, QBCC, Suitable Operators and ACT. All experiment approvals were synthetic, and the six sources were already development sources.

The current `outputs/` contains the complete acquisition-connected cohort; all six inputs produced its downstream results together. Historical r2 outputs remain in [the evaluation archive](../evidence/evaluation-traces.tar.gz) under `historical-r2/outputs/`, with their original bindings and [source traces](../evidence/six-source-r2.tar.zst). Both cohorts have zero human link reviews; labels cannot transfer between them. Historical absolute paths are provenance, not current resumable paths. The restored offline collection is a separate check and does not establish new model performance.
