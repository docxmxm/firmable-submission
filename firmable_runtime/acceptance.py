"""Caller-owned task requirements, independent of a candidate's chosen mappings."""

import copy

from .common import Failure, digest
from .engine import CANONICAL_FIELDS, canonical_valid


def freeze_requirements(request):
    """Validate trusted task scope before any model can propose a candidate."""
    fields = request.get("required_fields", [])
    if (
        not isinstance(fields, list)
        or any(
            not isinstance(field, str) or field not in CANONICAL_FIELDS
            for field in fields
        )
        or len(fields) != len(set(fields))
    ):
        raise Failure(
            "validation_error",
            "required_fields must be unique supported canonical fields",
        )
    return {
        "version": 1,
        "required_fields": sorted(fields),
        "authority": "trusted_inference_request_before_candidate_generation",
        "acceptance_rule": "Each declared field needs a validated mapping and at least one accepted informative value in the evaluated scope. Missing evidence remains unresolved; never invent a value to satisfy a requirement.",
        "limitation": "Field completion is not accuracy or recall over all extractable rows. Counts are reported separately, without an inferred non-null quota. No declared fields means task acceptance is not configured.",
    }


def evaluate_acceptance(
    contract, config, observations, report, *, scope, prior_acceptance=None
):
    """Assess observed results; a later extraction cannot clear prior uncertainty.

    The caller supplies actual execution reports, not mapper explanations. This
    function does not infer source roles or approve a mapping itself.
    """
    requirements_hash = digest(contract)
    config_hash = digest(config)
    prior_fields = {}
    if prior_acceptance is not None:
        if (
            prior_acceptance.get("requirements_hash") != requirements_hash
            or prior_acceptance.get("config_hash") != config_hash
        ):
            raise Failure(
                "checkpoint_mismatch",
                "Prior task acceptance belongs to different requirements or config",
            )
        prior_fields = {
            item["canonical_field"]: item for item in prior_acceptance["fields"]
        }
    config = config or {}
    mappings = config.get("field_mappings", [])
    output_records = {
        (row["source_id"], row["source_record_id"]) for row in observations
    }
    fields = []
    for target in contract["required_fields"]:
        mapping_indices = [
            index
            for index, mapping in enumerate(mappings)
            if mapping["canonical_field"] == target
        ]
        group, name = target.split(".")
        accepted = {
            (row["source_id"], row["source_record_id"])
            for row in observations
            if (value := row.get(group, {}).get(name)) != "unknown"
            and canonical_valid(target, value)
        }
        diagnostics = []
        candidate_not_executed = report.get("execution_status") == "not_executed"
        for error in report.get("errors", []):
            error_target = error.get("canonical_field")
            index = error.get("mapping_index")
            if (
                error_target == target
                or index in mapping_indices
                or (
                    error_target is None
                    and index is None
                    and not candidate_not_executed
                )
            ):
                diagnostics.append(
                    {
                        key: copy.deepcopy(error[key])
                        for key in (
                            "reason_code",
                            "message",
                            "repair_hint",
                            "review_verdict",
                            "mapping_index",
                            "canonical_field",
                            "source_fields",
                            "semantic_support",
                            "pointer",
                            "error_scope",
                            "validation_stage",
                            "reference_context",
                        )
                        if key in error
                    }
                )
        if not mapping_indices:
            status, reason = "needs_evidence", "required_field_mapping_not_established"
        elif diagnostics:
            uncertain = all(
                error.get("reason_code")
                in {
                    "name_role_uncertain",
                    "name_role_evidence_invalid",
                    "name_role_review_invalid",
                }
                for error in diagnostics
            )
            status = "needs_evidence" if uncertain else "failed"
            reason = (
                "mapping_evidence_unresolved"
                if uncertain
                else "mapping_validation_failed"
            )
        elif candidate_not_executed:
            status, reason = "needs_evidence", "candidate_not_executed"
        elif not accepted:
            status, reason = "needs_evidence", "no_informative_values_observed"
        else:
            status, reason = "complete", "validated_mapping_with_accepted_values"
        item = {
            "canonical_field": target,
            "status": status,
            "reason_code": reason,
            "mapping_count": len(mapping_indices),
            "accepted_value_count": len(accepted),
            "enveloped_record_count": len(output_records),
            "accepted_record_fraction": len(accepted) / len(output_records)
            if output_records
            else None,
            "diagnostics": diagnostics,
        }
        prior = prior_fields.get(target)
        if prior is not None and prior["status"] != "complete":
            item["prior_unresolved"] = {
                "status": prior["status"],
                "reason_code": prior["reason_code"],
                "scope": prior_acceptance["scope"],
            }
            if item["status"] != "failed":
                item["status"], item["reason_code"] = (
                    prior["status"],
                    prior["reason_code"],
                )
        fields.append(item)
    unresolved = [
        item["canonical_field"] for item in fields if item["status"] != "complete"
    ]
    return {
        "status": "not_configured"
        if not fields
        else "partial"
        if unresolved
        else "complete",
        "scope": scope,
        "requirements_hash": requirements_hash,
        "config_hash": config_hash,
        "fields": fields,
        "unresolved_fields": unresolved,
        "human_gold": False,
        "limitation": contract["limitation"],
    }


def unresolved_history(candidates):
    """Retain failed/uncertain attempts after a later candidate omits a field."""
    history = []
    for ident, candidate in sorted(
        candidates.items(), key=lambda pair: (pair[1]["decision_turn"], pair[0])
    ):
        for scope, feedback, execution_ref in (
            ("proposal", candidate.get("feedback", {}), candidate["probe_ref"]),
            (
                "development_validation",
                candidate.get("validation_feedback", {}),
                candidate.get("validation_ref"),
            ),
        ):
            acceptance = feedback.get("task_acceptance", {})
            for item in acceptance.get("fields", []):
                if item["status"] != "complete":
                    history.append(
                        {
                            "candidate_id": ident,
                            "candidate_config_hash": candidate["config_hash"],
                            "probe_ref": copy.deepcopy(candidate["probe_ref"]),
                            "scope": acceptance.get("scope", scope),
                            "execution_ref": copy.deepcopy(execution_ref),
                            **copy.deepcopy(item),
                        }
                    )
    return {
        "entries": history,
        "authority": "Historical execution findings and fallible model reviews, not source evidence or permission to emit unsupported values.",
    }
