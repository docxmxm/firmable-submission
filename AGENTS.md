# Working on this repository

Read `README.md` and `docs/ARCHITECTURE.md` first. `firmable_runtime.workflow` is the public API and `python -m firmable_runtime` is the CLI.

- Keep one runtime and one response contract. Do not create version/release directories or source-specific extraction branches.
- The current six configs and observations are the complete pre-scope r2 cohort. Preserve their provenance and matching runtime identity; do not mix source-wise best runs or silently replace saved evidence during a refactor.
- Improve general inference/execution capability. Do not edit mappings, aliases or native rows merely to pass evaluation.
- Preserve raw records, stable locators, typed identifiers, subject attribution, field provenance and explicit abstention. Model judgments and validation flags cannot authorize ingestion.
- Paid model calls require explicit user authorization for their scope. Reuse authorization already given; do not start paid runs as part of offline verification. Never resend an unknown outcome. Retain safe failed-call receipts and never commit credentials or raw CLI envelopes.
- Approval binds config, evidence, ontology and runtime. Preserve immutable artifacts and recovery boundaries. Synthetic approvals remain labelled synthetic. Current link precision is unknown until this cohort is human-reviewed; prior cohorts' labels do not transfer.
- Run `uv run python -m firmable_runtime check` after behavior changes. Tests use temporary fixtures and scripted providers with no paid calls. Static check: `uvx ruff check firmable_runtime scripts tests --select F401,F821,F811,F841`.
- Keep documentation and compact evidence in their existing locations. Do not introduce another baseline, copied repository or external experiment dependency.
