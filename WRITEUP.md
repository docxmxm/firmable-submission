# Schema inference and entity identification

The runtime generated six configs and 22,925 observations. Frozen source-agreement checks retained 389 Companies names, 888 business names and 8,184 Suitable Operators types, with no labelled wrong values or misses. These are narrow labels, not human gold. Four sources still lack legal names. Small Companies type variation (389 → 386) is accepted. A new 149-row source produced no config after document-fetch failures. This is a working partial submission; generalisation and human link precision remain unproven. [Detailed results](RESULTS.md).

## 1. Cost

For Companies, onboarding used **14 model calls, 383,438 tokens, USD 0.0524642 and 178.651 seconds**, including evidence acquisition. Across six sources: 87 calls, 1,563,821 tokens, USD 0.2054033 and 262.234 elapsed seconds with three workers. Extraction executed AST rules: zero per-record API calls. The new-source attempt cost USD 0.0063431. This change totalled USD 0.2117464; all retained reference/repair evaluations total USD 0.8179238. These are provider receipts, not invoices or total historical project spend. Engineering time and compliance with the original six-hour time box are unverified.

## 2. Agent design and weakest step

The agent chooses observed source links, reads documents, inspects tables, separates proposal/development records, proposes mappings and executable rules, probes them, reviews name roles, validates on initially withheld records, and revises/selects a config. Separate steps expose failures and carry evidence forward. Only original excerpts supplement evidence; model explanations cannot become definitions. The host supplies diagnostics and execution changes rather than hand-writing repairs. Human review shows mappings, quotations, examples, omissions and unresolved requirements; approval binds evidence, config, ontology and runtime.

Immutable requests, results and checkpoints allow recovery at completed steps. Unknown paid outcomes stop without resend. Document-fetch failure retains usable local inputs. Acquisition currently precedes inference rather than restarting after every candidate failure.

Useful-evidence acquisition is weakest: seven sources made six fetch attempts, two succeeded, but none yielded a new exported excerpt. The new source's metadata failed TLS verification and its program URL returned 404; the agent then stopped without a mapping. Its zero positive labels also limit evaluation. The connection and failure feedback work, but fetched-evidence-driven improvement is unproven. The 407 offline tests include that full path with scripted providers, not a model-quality claim.

## 3. The hard one

ATO mixes business and explanatory workbook tables. The current run kept 4,436 business rows but lacks legal names; an earlier run included six explanatory rows. Selecting all readable tables still skips separate scope review. I would improve scope reasoning and source-document retrieval. Custom code is appropriate for a documented unsupported format/transformation when a reusable reader is uneconomical: isolate, test and disclose the adapter. Missing semantics should remain unresolved rather than be invented by a source-specific parser.

## 4. Scale

At 500 sources, missing documentation and reviewer workload likely constrain onboarding first. Dense identifier groups create pairwise work; copied evidence increases storage. Measure review time, calls, recall and operational failures. Partition candidates by typed identifiers and keep deterministic extraction separate from onboarding. This is a scaling hypothesis, not a benchmark.

## 5. Relationships and matcher change

Typed shared identifiers create candidate pairs; accepted pairs form canonical groups. Subject/name conflicts produce an unlinked queue. All 29 historical risk pairs remain rejected, but they are not human-confirmed false links. Parent/subsidiary hints do not establish identity and are left unasserted. The fixed 0.99 score is uncalibrated; the 50-pair review pack awaits human decisions, so measured precision is unavailable.

The build has 225 groups and 50 profiles with per-field provenance, confidence and losing claims. Withdrawal recomputes fields while holding membership fixed; it flags identity review rather than proving the remaining cluster. See the retained withdrawal result in `outputs/withdrawal/`.

For a replacement matcher, retain raw locators, observations, source/config/model/runtime hashes, policy versions and human decisions. Run both matchers on a frozen snapshot, compare changed pairs and cluster splits/merges, review risky deltas, activate bounded batches, and roll back memberships with profiles. This is a migration plan, not a demonstrated deployment.

## 6. Next three weeks

First improve reliable retrieval and useful-document discovery, then test whether actual original excerpts improve decisions on independently evaluable new sources. Complete human review of linked and abstained populations. Measure documentation drift and reviewer workload before testing matcher migration. Preserve failures and useful partial outputs; do not hand-write schemas or add gates merely to manufacture certainty.
