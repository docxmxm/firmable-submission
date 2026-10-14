# Working on this repository

Read `README.md` and `docs/ARCHITECTURE.md` first. `firmable_runtime.workflow` is the public API and `python -m firmable_runtime` is the CLI.

- Keep one runtime and one response contract. Do not create version/release directories or source-specific extraction branches.
- The active submission is bound in evidence/current-submission.json: unchanged model-generated configs, current-runtime native replay and downstream artifacts, plus a complete bound human audit. The prior acquisition-connected cohort is preserved at Git commit f673ff961b8cceb7afb3cf2fdeaa96998d0ed46e and in its original receipts; historical r2 outputs remain in evidence/evaluation-traces.tar.gz under historical-r2/outputs/. Preserve all provenance and runtime identities; do not mix source-wise best runs or silently replace evidence.
- Improve general inference/execution capability. Do not edit mappings, aliases or native rows merely to pass evaluation.
- Preserve raw records, stable locators, typed identifiers, subject attribution, field provenance and explicit abstention. Model judgments and validation flags cannot authorize ingestion.
- Paid model calls require explicit user authorization for their scope. Reuse authorization already given; do not start paid runs as part of offline verification. Never resend an unknown outcome. Retain safe failed-call receipts and never commit credentials or raw CLI envelopes.
- Approval binds config, evidence, ontology and runtime. Preserve immutable artifacts and recovery boundaries. Synthetic approvals remain labelled synthetic. Human precision is bound to the exact current sample, population and evidence; changed cases need new review. Prior cohorts' labels do not transfer without explicit evidence equality and retained authorship.
- Run `uv run python -m firmable_runtime check` after behavior changes. Tests use temporary fixtures and scripted providers with no paid calls. Static check: `uvx ruff check firmable_runtime scripts tests --select F401,F821,F811,F841`.
- Keep documentation and compact evidence in their existing locations. Do not introduce another baseline, copied repository or external experiment dependency.
