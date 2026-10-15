# Frozen fresh-source attempt: AEMO NEM registration

**Outcome: failed before candidate generation. This does not establish successful autonomous onboarding.** The prior six-source submission and completed human link audit remain unchanged.

The current code was frozen at commit `a4e39829ba540b8294c2bbdf23b1bb2b6333e71a`, runtime `9aa20f509bd42a8f9844cb8c7a051407e16a2becc946010b1863b8ef6cb9ba28`, before source selection and native inspection. All 81 recorded code, test, dependency and ontology files remained unchanged. There was one source and one trajectory, with no hand-edited mapping, source patch, replacement source or retry.

## Selection and evidence

The [AEMO NEM Registration and Exemption List](https://www.aemo.com.au/energy-systems/electricity/national-electricity-market-nem/participate-in-the-market/registration), dated 13 October 2026, was selected using metadata and feasible public download availability. No matching source was found in the retained local tuning histories searched. This is not a random-source experiment or proof about deleted history, entity overlap or model pretraining.

The original 785,036-byte XLSX is preserved unchanged: SHA-256 `6b264b17fb6aeb70891c9a2b3e120bbbd99ab032c67d3d586039eec06ccbf0ac`. Original publisher metadata was transcribed from the web-rendered page; direct HTML access returned HTTP 403. This transcription is identified explicitly and contains no added mapping advice. The minimum required field was ABN, based on publisher metadata; no config or evaluation labels were supplied to the agent.

## Actual trajectory and cost

| Stage | Result |
|---|---|
| Source-document acquisition | Five model calls; zero exported excerpts. XLSX static-text capture and HTML fetch failed. Local workbook and supplied publisher prose remained available. |
| Native discovery | 13 sheets inventoried, 10 readable tables. The two main business sheets were `no_unambiguous_table`; Overview was also unreadable as a table. |
| Model inspection | One successful model response selected all readable tables plus the two unreadable business sheets. |
| Host validation | `validation_error: Selected table is not a discovered native table`. Execution ended before proposal generation. |
| Config/probe/revision/review/approval/extraction | Not reached. Zero validated configs, approvals or formal observations. |

Total: **6 calls, 63,232 input tokens, 2,773 output tokens (66,005 total), US$0.0077097 and 21.869 seconds**. All six provider responses were HTTP 200, complete and known, with reported costs. No unknown outcome, resend or budget stop occurred. Costs are provider receipts, not an invoice.

The preset limits were 40 calls, 600 seconds for new sends, and a US$0.50 receipt-based cost guard including US$0.25 per in-flight reservation. That guard is not a provider-enforced billing ceiling. The run stopped on the selection error, well before any guard.

## Why it failed

Three distinct boundaries contributed:

1. **Reader:** the main registration sheet has 21 header columns while later rows reach column 22. The intermediary sheet has two ABN headers for different actors. The existing header heuristic requires unique nonempty headers and later rows fitting their width, so it does not expose either sheet as usable records.
2. **Model/contract:** the model selected those IDs despite their unreadable status. Inspection instructions favour retaining uncertain business scope; the host permits only readable table IDs. This is a contract risk, not evidence that every model would make the same choice.
3. **Recovery:** the initial selection check raises before the proposal/revision loop. The refusal is not returned as repair feedback. Rejecting unsupported tables is safe; silently skipping them would still leave a coverage gap.

The model never reached field or name mapping. A stronger model was not tested and cannot be claimed to solve the reader limitation. For the complete system, this is a failed new-source onboarding attempt.

## Independent checks and limits

An independent openpyxl audit reproduced the frozen source-value opportunities and verified all frozen hashes, the original workbook, six safe receipts, the invalid selected IDs, and absence of config/approval/output. The pre-inference ABN cell inventory is not ownership or legal-name gold. A post-run clarification separates multiple-actor ABN cells; its initially inaccurate timing wording is explicitly corrected in the retained evidence. No field precision, recall, or output-fidelity score is computed: these are **N/A**, not successful abstention or zero-error accuracy.

Next work should address generic table boundaries, duplicate-header actor attribution and recoverable selection feedback, followed by another genuinely new holdout. This source is now exposed and would only be regression evidence after a repair.

## Inspect the evidence without model calls

The [machine receipt](../../../evidence/fresh-holdout-20261015.json) summarises the outcome. The [full evidence archive](../../../evidence/fresh-holdout-20261015-trace.tar.gz) contains the original workbook, selection/freeze records, native discovery, all model requests/responses, safe receipts, checkpoint, acquisition journal, independent audit and explicit timing correction. It excludes credentials and unrelated working directories.

Extract the archive into the repository root, then run `python runs/fresh-holdout-20261015/audit.py` using Python with openpyxl and the repository dependencies. This is read-only with respect to inputs/code and makes no model calls; it recreates `audit.json`. Never resume the paid run to reproduce this failure.

The workbook was created and published by **Australian Energy Market Operator (AEMO)**. AEMO's [Copyright Permissions](https://www.aemo.com.au/privacy-and-legal-notices/copyright-permissions) permit attributed use of its public material subject to the stated exclusions. Permission was checked after the trial; the frozen request's original unknown licence value remains unchanged.
