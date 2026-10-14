"""Bounded evidence and output-branch repair proposals, never approval."""

import copy

from .common import Failure, digest
from .config_audit import (
    NAME_TARGETS,
    MAX_TRACE_BYTES,
    _branch_evidence,
    _branch_findings,
    _coverage_findings,
    _group_evidence,
    _review_evidence,
    review_schema,
)
from .execution import pointer_get
from .engine import ast_nodes, compile_mappings
from .schema import check_response


def repair_evidence_bindings(config, errors, documentation):
    """Try the exact original primary header through the unchanged compiler.

    Only the evidence reference can change. No aliases, inferred definitions,
    parser changes or name-review waivers are introduced. Every accepted local
    proposal still needs full candidate compilation, execution and review.
    """
    revised = copy.deepcopy(config)
    if config.get("execution", {}).get("kind", "ast") != "ast":
        return revised, errors, []
    inventory = [f["source_field"] for f in config["source_schema"]["fields"]]
    changes = []
    for index, mapping in enumerate(config["field_mappings"]):
        findings = [e for e in errors if e.get("mapping_index") == index]
        if not findings or any(
            e.get("reason_code") not in {"semantic_rule", "exact_quote"}
            or e.get("canonical_field") != mapping["canonical_field"]
            or e.get("source_fields") != mapping["source_fields"]
            or e.get("error_scope") != "mapping"
            for e in findings
        ):
            continue
        primary = mapping["source_field"]
        if primary not in inventory or mapping["semantic_evidence"] == primary:
            continue
        candidate = copy.deepcopy(mapping)
        candidate["semantic_evidence"] = primary
        try:
            candidate["ast"] = pointer_get(config, mapping["execution_pointer"])
            compile_mappings(
                [candidate], inventory, documentation,
                [{"source_field": f, "reason": "Outside this isolated mapping check."}
                 for f in inventory if f not in mapping["source_fields"]],
            )
        except (Failure, KeyError, TypeError, ValueError):
            continue
        revised["field_mappings"][index]["semantic_evidence"] = primary
        revised["field_mappings"][index]["validation_pass"] = False
        changes.append({
            "mapping_index": index,
            "pointer": f"/field_mappings/{index}/semantic_evidence",
            "previous_evidence_hash": digest(mapping["semantic_evidence"]),
            "original_header": primary,
            "basis": "unchanged_compiler_accepts_exact_original_primary_header",
        })
    repaired = {c["mapping_index"] for c in changes}
    return revised, [e for e in errors if e.get("mapping_index") not in repaired], changes


def _output_branch(ast, path):
    """Only direct when arms: omission cannot activate another output fallback."""
    parts = path.split("/")[1:]
    if not parts or any(part not in {"then", "else"} for part in parts):
        return None
    node = ast
    for part in parts:
        if node.get("op") != "when":
            return None
        node = node[part]
    return node


def _name_branch_cuts(config, documentation, audit):
    """Recheck immutable review bindings, quotes and structural coverage."""
    expected = {
        "candidate_config_hash": digest(config),
        "documentation_hash": digest(documentation),
        "scope": "independent_name_role_model_review",
        "context_policy": "current_candidate_only",
        "human_gold": False,
    }
    if not isinstance(audit, dict) or any(
        audit.get(k) != v for k, v in expected.items()
    ):
        return {}
    mappings = {
        i: m
        for i, m in enumerate(config["field_mappings"])
        if m["canonical_field"] in NAME_TARGETS
    }
    derived = {"evidence_verified", "branch_analysis_verified", "coverage_evidence"}
    response = {
        "reviews": [
            {k: v for k, v in r.items() if k not in derived}
            for r in audit.get("reviews", [])
        ]
    }
    check_response(response, review_schema(list(mappings)))
    reviews = response["reviews"]
    if sorted(r["mapping_index"] for r in reviews) != sorted(mappings):
        return {}
    inventory = {f["source_field"] for f in config["source_schema"]["fields"]}
    proposed = []
    for i, m in mappings.items():
        ast = pointer_get(config, m["execution_pointer"])
        proposed.append(
            {
                "mapping_index": i,
                "canonical_field": m["canonical_field"],
                "source_fields": m["source_fields"],
                "executable": ast,
                "host_branch_evidence": _branch_evidence(
                    m, ast, [], [], inventory, "ast", MAX_TRACE_BYTES
                ),
            }
        )
    grouped = _group_evidence(proposed, [], [], "ast")
    cuts = {}
    for review in reviews:
        i = review["mapping_index"]
        m = mappings[i]
        host = next(p for p in proposed if p["mapping_index"] == i)
        refs, invalid = _review_evidence(review, m, documentation, inventory)
        invalid += _branch_findings(
            review, host["host_branch_evidence"], m, documentation, inventory
        )
        invalid += _coverage_findings(review, proposed, grouped, reviews, [], "ast")
        if invalid or not refs or review["verdict"] == "supported":
            continue
        positive = [
            b["branch_id"]
            for b in review["branch_reviews"]
            if b["verdict"] == "supported" and b["expected_value_fields"]
        ]
        if not positive:
            continue
        for b in review["branch_reviews"]:
            path = b["branch_id"]
            node = _output_branch(host["executable"], path)
            # A known current-value obligation needs model repair, not deletion.
            # Uncertain coverage also remains unresolved instead of becoming proof.
            if (
                b["verdict"] not in {"uncertain", "unsupported"}
                or b["current_value_requirement"] != "not_documented"
                or b["current_value_fields"]
                or b["coverage_mapping_indices"]
                or not b["evidence_quotes"]
                or node is None
                or node["op"] == "omit"
                or any(p == path or p.startswith(path + "/") for p in positive)
            ):
                continue
            cuts.setdefault(i, []).append(path)
    return cuts


def repair_output_branches(config, errors, documentation, audit):
    """Keep predicates and supported siblings; all returned rules need new review.

    This deliberately does not solve arbitrary AST equivalence or use old model
    confidence as truth. Whole-group fallback remains for unattributable errors.
    """
    original = copy.deepcopy(config)
    if config.get("execution", {}).get("kind", "ast") != "ast":
        return original, errors, []
    try:
        cuts = _name_branch_cuts(config, documentation, audit)
    except (Failure, KeyError, TypeError, ValueError):
        return original, errors, []
    remaining, changes = [], []
    for i, paths in list(cuts.items()):
        # Keep only the outermost non-overlapping safe cuts.
        cuts[i] = [
            p
            for p in sorted(set(paths), key=lambda p: (len(p), p))
            if not any(p.startswith(q + "/") for q in paths if q != p)
        ]
    for i, paths in list(cuts.items()):
        m = config["field_mappings"][i]
        if (
            sum(
                peer["execution_pointer"] == m["execution_pointer"]
                for peer in config["field_mappings"]
            )
            != 1
        ):
            del cuts[i]
            continue
        ast = copy.deepcopy(pointer_get(config, m["execution_pointer"]))
        for path in paths:
            parent, arm = path.rsplit("/", 1)
            node = ast
            for part in parent.split("/")[1:]:
                node = node[part]
            node[arm] = {"op": "omit", "reason": "Unresolved branch"}
        dependencies = {n["name"] for _, n in ast_nodes(ast) if n["op"] == "field"}
        if m["source_field"] not in dependencies:
            del cuts[i]  # Do not invent a new primary field/evidence binding.
    for error in errors:
        i = error.get("mapping_index")
        paths = cuts.get(i, [])
        covered = (
            bool(paths)
            and error.get("rule") == "independent_name_role_review"
            and error.get("reason_code")
            in {"name_role_uncertain", "name_role_unsupported"}
        )
        unsupported = error.get("semantic_support", {}).get("name_branch_support", [])
        if paths and error.get("reason_code") == "semantic_rule" and unsupported:
            covered = all(
                any(
                    b.get("pointer") == p or b.get("pointer", "").startswith(p + "/")
                    for p in paths
                )
                for b in unsupported
            )
        if not covered:
            remaining.append(error)
    blocked = {e.get("mapping_index") for e in remaining}
    for i, paths in cuts.items():
        if i in blocked:
            continue
        mapping = original["field_mappings"][i]
        for path in paths:
            full = mapping["execution_pointer"] + path
            node = pointer_get(config, full)
            parent, arm = full.rsplit("/", 1)
            pointer_get(original, parent)[arm] = {
                "op": "omit",
                "reason": "Unresolved output branch; source values remain in raw_record.",
            }
            changes.append(
                {
                    "mapping_index": i,
                    "branch_id": path,
                    "pointer": full,
                    "removed_expression_hash": digest(node),
                }
            )
        mapping["transformation"] = (
            "Host removed only the bound unresolved output branches "
            + ", ".join(paths)
            + ". Original conditions and other expressions are unchanged; fresh validation and semantic review are required."
        )
        mapping["validation_pass"] = False
        dependencies = {
            n["name"]
            for _, n in ast_nodes(pointer_get(original, mapping["execution_pointer"]))
            if n["op"] == "field"
        }
        mapping["source_fields"] = [
            f for f in mapping["source_fields"] if f in dependencies
        ]
    used = {f for m in original["field_mappings"] for f in m["source_fields"]}
    for slot in ("observed_time", "valid_from", "valid_to"):
        used.update(original["execution"].get(slot, {}).get("source_fields", []))
    before = {f for m in config["field_mappings"] for f in m["source_fields"]}
    unmapped = {m["source_field"] for m in original["unmapped_fields"]}
    for field in sorted(before - used - unmapped):
        original["unmapped_fields"].append(
            {
                "source_field": field,
                "reason": "Unresolved after local branch removal; original values remain in raw_record.",
            }
        )
    return original, remaining, changes
