"""Compile and execute bounded transformations over native record DTOs.

The compiler rejects unsupported field dependencies and known subject conflicts;
model confidence never bypasses the native, typed or provenance checks.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timezone

from .common import Failure, utc_now
from .native import field_state, field_value

ENGINE_VERSION = "mvp-engine-0.9.27"
DSL_VERSION = "bounded-ast-0.4"
MAX_AST_NODES = 128
MAX_AST_DEPTH = 16


ENUMS = {
    "entity.entity_type": {
        "company",
        "sole_trader",
        "partnership",
        "trust",
        "incorporated_association",
        "government",
        "other",
        "unknown",
    },
    "entity.status": {
        "active",
        "deregistered",
        "in_liquidation",
        "suspended",
        "unknown",
    },
    "address.state": {
        "NSW",
        "VIC",
        "QLD",
        "WA",
        "SA",
        "TAS",
        "ACT",
        "NT",
        "NZ",
        "OTHER",
    },
}

CANONICAL_FIELDS = {
    "entity.legal_name",
    "entity.trading_name",
    "entity.abn",
    "entity.acn",
    "entity.nzbn",
    "entity.entity_type",
    "entity.status",
    "entity.website",
    "entity.industry_code",
    "entity.date_registered",
    "address.full",
    "address.locality",
    "address.state",
    "address.postcode",
    "address.country",
}

AST_KEYS = {
    "field": {"op", "name"},
    "trim": {"op", "input"},
    "collapse_ws": {"op", "input"},
    "parse_typed_id": {"op", "input", "kind", "require_label"},
    "parse_mixed_au_id": {"op", "input", "kind"},
    "parse_au_location": {"op", "input", "component"},
    "strip_id_separators": {"op", "input"},
    "validate_abn": {"op", "input"},
    "validate_acn": {"op", "input"},
    "parse_date": {"op", "input", "format"},
    "parse_datetime": {"op", "input", "format", "timezone"},
    "enum_lookup": {"op", "input", "values", "default"},
    "join_address": {"op", "inputs", "require_all", "on_missing", "separator"},
    "omit": {"op", "reason"},
    "when": {"op", "condition", "then", "else"},
    "is_present": {"op", "input"},
    "equals": {"op", "input", "value"},
    "in": {"op", "input", "values"},
}


def valid_abn(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{11}", value):
        return False
    digits = list(map(int, value))
    digits[0] -= 1
    return (
        sum(a * b for a, b in zip(digits, [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19])) % 89
        == 0
    )


def valid_acn(value):
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[0-9]{9}", value))
        and (10 - sum(int(value[i]) * (8 - i) for i in range(8)) % 10) % 10
        == int(value[8])
    )


def parse_typed_identifier(value, kind, require_label=False):
    """One whole ABN/ACN text, with a required or optional matching type label."""
    if (
        not isinstance(kind, str)
        or kind not in {"abn", "acn"}
        or not isinstance(require_label, bool)
        or not isinstance(value, (str, int))
        or isinstance(value, bool)
    ):
        return None
    label = r"(?:" + kind + r"(?:\s*:\s*|\s+))" + ("" if require_label else "?")
    matched = re.fullmatch(label + r"([0-9][0-9 \t-]*)", str(value).strip(), re.I)
    if not matched:
        return None
    digits = re.sub(r"[ \t-]", "", matched.group(1))
    return (
        digits if (valid_abn(digits) if kind == "abn" else valid_acn(digits)) else None
    )


def parse_au_location(value, component):
    """Extract one explicit component of a whole AU locality/state/postcode tuple.

    This finite grammar neither geocodes a place nor infers state from postcode.
    Street addresses, multiple locations and truncated tuples are abstentions.
    """
    if (
        not isinstance(value, str)
        or not isinstance(component, str)
        or component not in {"locality", "state", "postcode"}
        or len(value) > 256
    ):
        return None
    # Keep the comma-delimited and whitespace-delimited forms separate: mixed
    # punctuation, additional address parts and cross-line prose are ambiguous.
    if re.search(r"[\r\n]", value):
        return None
    text = value.strip()
    locality = r"([^\W\d_][^\d,;:/|]*?)"
    state = r"(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)"
    pattern = (
        locality + r"[ \t]*,[ \t]*" + state + r"[ \t]*,[ \t]*([0-9]{4})"
        if "," in text
        else locality + r"[ \t]+" + state + r"[ \t]+([0-9]{4})"
    )
    matched = re.fullmatch(pattern, text, re.I)
    if not matched:
        return None
    place = " ".join(matched[1].split())
    # Localities may contain spaces, apostrophes, hyphens and a St. abbreviation;
    # punctuation with prose/address meaning or a bare state is not a locality.
    if (
        not re.fullmatch(r"[^\W\d_]+(?:['’\-][^\W\d_]+|\.? [^\W\d_]+)*\.?", place)
        or place.upper()
        in {
            "NSW",
            "VIC",
            "QLD",
            "WA",
            "SA",
            "TAS",
            "ACT",
            "NT",
            "UNKNOWN",
            "UNSPECIFIED",
            "NOT AVAILABLE",
            "NOT KNOWN",
        }
        or re.search(
            r"\b(?:STREET|ROAD|RD|AVENUE|AVE|DRIVE|DR|BOULEVARD|BLVD|LANE|LN|COURT|CT|CRESCENT|CRES|HIGHWAY|HWY)$",
            place,
            re.I,
        )
    ):
        return None
    return {"locality": place, "state": matched[2].upper(), "postcode": matched[3]}[
        component
    ]


class CompileDiagnostics:
    """Collect bounded errors; any error rejects the complete configuration."""

    def __init__(self, limit=128):
        self.limit, self.findings, self.finding_count = limit, [], 0

    def add(self, code, message, pointer, mapping_index=None, **details):
        self.finding_count += 1
        if len(self.findings) < self.limit:
            item = {
                "error_type": "validation_error",
                "code": code,
                "message": message,
                "pointer": pointer,
                **details,
            }
            if mapping_index is not None:
                item["mapping_index"] = mapping_index
            self.findings.append(item)

    def finish(self):
        if self.finding_count:
            raise Failure(
                "validation_error",
                "Static compilation failed: "
                + "; ".join(e["message"] for e in self.findings[:3]),
                findings=self.findings,
                finding_count=self.finding_count,
            )


def compile_ast(
    ast,
    available_fields,
    declared_fields,
    *,
    diagnostics=None,
    pointer="",
    mapping_index=None,
):
    own = diagnostics is None
    diagnostics = diagnostics or CompileDiagnostics()
    seen_fields, paths = set(), []
    complete = True

    def issue(code, message, at, **details):
        diagnostics.add(code, message, (pointer + at) or "/", mapping_index, **details)

    def walk(node, at="", depth=0):
        nonlocal complete
        if depth > MAX_AST_DEPTH or len(paths) >= MAX_AST_NODES:
            issue("ast_bound", "AST exceeds bounded depth/node limit", at)
            complete = False
            return
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("op"), str)
            or node["op"] not in AST_KEYS
        ):
            issue("unknown_operation", "Unknown DSL operation", at)
            complete = False
            return
        op = node["op"]
        if set(node) - AST_KEYS[op]:
            issue(
                "unknown_ast_keys",
                "Undeclared AST keys for " + op,
                at,
                keys=sorted(set(node) - AST_KEYS[op]),
            )
        paths.append(at or "/")
        if op == "field":
            name = node.get("name")
            if not isinstance(name, str) or name not in available_fields:
                issue(
                    "nonexistent_dependency",
                    "AST field is not in the native inventory",
                    at + "/name",
                    source_field=name,
                )
            if isinstance(name, str):
                seen_fields.add(name)
                if name not in declared_fields:
                    issue(
                        "undeclared_dependency",
                        "AST consumes a field absent from source_fields",
                        at + "/name",
                        source_field=name,
                    )
        elif op == "omit":
            if not isinstance(node.get("reason"), str) or not node["reason"].strip():
                issue("omit_reason", "Omit must explain its reason", at + "/reason")
        elif op == "join_address":
            inputs = node.get("inputs")
            if not isinstance(inputs, list) or not 2 <= len(inputs) <= 12:
                issue("join_arity", "Address join requires 2–12 inputs", at + "/inputs")
            if node.get("require_all") is not True or node.get("on_missing") != "omit":
                issue(
                    "join_missing_policy",
                    "Address joins must require all declared parts",
                    at,
                )
            if (
                not isinstance(node.get("separator", ", "), str)
                or len(node.get("separator", ", ")) > 8
            ):
                issue(
                    "join_separator",
                    "Address separator must be bounded text",
                    at + "/separator",
                )
            if isinstance(inputs, list):
                for index, item in enumerate(inputs[:12]):
                    walk(item, at + "/inputs/" + str(index), depth + 1)
                if len(inputs) > 12:
                    complete = False
            else:
                complete = False
        elif op == "when":
            if (
                not isinstance(node.get("condition"), dict)
                or not isinstance(node["condition"].get("op"), str)
                or node["condition"]["op"] not in {"is_present", "equals", "in"}
            ):
                issue("predicate_kind", "Unsupported predicate", at + "/condition")
            for key in ("condition", "then", "else"):
                walk(node.get(key), at + "/" + key, depth + 1)
        else:
            walk(node.get("input"), at + "/input", depth + 1)
            if op == "parse_typed_id" and (
                not isinstance(node.get("kind"), str)
                or node.get("kind") not in {"abn", "acn"}
                or not isinstance(node.get("input"), dict)
                or node["input"].get("op") != "field"
            ):
                issue(
                    "typed_identifier_shape",
                    "parse_typed_id requires kind abn/acn and one direct native field input",
                    at,
                )
            if (
                op == "parse_typed_id"
                and "require_label" in node
                and not isinstance(node["require_label"], bool)
            ):
                issue(
                    "typed_identifier_shape",
                    "parse_typed_id require_label must be a boolean",
                    at + "/require_label",
                )
            if op == "parse_mixed_au_id" and (
                not isinstance(node.get("kind"), str)
                or node["kind"] not in {"abn", "acn"}
                or not isinstance(node.get("input"), dict)
                or node["input"].get("op") != "field"
            ):
                issue(
                    "mixed_identifier_shape",
                    "parse_mixed_au_id requires kind abn/acn and one direct native field input",
                    at,
                )
            if op == "parse_au_location" and (
                not isinstance(node.get("component"), str)
                or node["component"] not in {"locality", "state", "postcode"}
                or not isinstance(node.get("input"), dict)
                or node["input"].get("op") != "field"
            ):
                issue(
                    "location_component_shape",
                    "parse_au_location requires component locality/state/postcode and one direct native field input",
                    at,
                )
            if op == "enum_lookup":
                values = node.get("values")
                if not isinstance(values, dict) or len(values) > 100:
                    issue(
                        "enum_table",
                        "Enum lookup requires a bounded dictionary",
                        at + "/values",
                    )
                elif any(
                    not isinstance(k, str) or not isinstance(v, str)
                    for k, v in values.items()
                ):
                    issue(
                        "enum_types",
                        "Enum keys and values must be text",
                        at + "/values",
                    )
                if node.get("default") not in (None, "unknown"):
                    issue(
                        "enum_default",
                        "Only unknown or omission (null) is a default; other defaults assert unsupported facts",
                        at + "/default",
                        supplied_default=node.get("default"),
                    )
            if op == "in" and (
                not isinstance(node.get("values"), list) or len(node["values"]) > 100
            ):
                issue(
                    "predicate_values",
                    "in predicate requires a bounded list",
                    at + "/values",
                )
            if op.startswith("parse_") and "format" in node:
                fmt = node["format"]
                if not isinstance(fmt, str) or len(fmt) > 60 or "%Z" in fmt:
                    issue("date_format", "Unsupported date format", at + "/format")

    walk(ast)
    if complete and set(declared_fields) != seen_fields:
        issue(
            "dependency_mismatch",
            "source_fields must exactly match executable dependencies",
            "",
            declared_fields=sorted(declared_fields),
            actual_fields=sorted(seen_fields),
            unused_declared=sorted(set(declared_fields) - seen_fields),
            undeclared=sorted(seen_fields - set(declared_fields)),
        )
    if own:
        diagnostics.finish()
    return paths


def ast_nodes(ast, pointer=""):
    """Walk only the already-compiled DSL grammar, never data dictionaries."""
    yield pointer, ast
    if ast["op"] == "when":
        children = [("/" + key, ast[key]) for key in ("condition", "then", "else")]
    elif ast["op"] == "join_address":
        children = [
            ("/inputs/" + str(index), child)
            for index, child in enumerate(ast["inputs"])
        ]
    elif "input" in AST_KEYS[ast["op"]]:
        children = [("/input", ast["input"])]
    else:
        children = []
    for suffix, child in children:
        yield from ast_nodes(child, pointer + suffix)


def eval_ast(ast, raw, counts=None, pointer=""):
    if counts is not None:
        counts[pointer or "/"] += 1

    def child(node, suffix):
        return eval_ast(node, raw, counts, pointer + suffix)

    op = ast["op"]
    if op == "field":
        value = field_value(raw, ast["name"])
        return value
    if op == "omit":
        return None
    if op == "when":
        branch = "then" if child(ast["condition"], "/condition") else "else"
        return child(ast[branch], "/" + branch)
    if op == "join_address":
        values = [
            child(item, "/inputs/" + str(i)) for i, item in enumerate(ast["inputs"])
        ]
        if any(value is None or not str(value).strip() for value in values):
            return None
        return ast.get("separator", ", ").join(str(value).strip() for value in values)
    value = child(ast["input"], "/input")
    if op in {"is_present", "equals", "in"}:
        result = (
            value is not None and value != "" and bool(str(value).strip())
            if op == "is_present"
            else value == ast.get("value")
            if op == "equals"
            else value in ast["values"]
        )
        return result
    if value is None or value == "" or not str(value).strip():
        return None
    if op == "enum_lookup":
        return ast["values"].get(str(value), ast.get("default"))
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise Failure(
            "validation_error",
            "Transformation requires a scalar text value",
            reason_code="non_scalar_input",
            ast_pointer=pointer or "/",
        )
    value = str(value)
    if op == "trim":
        return value.strip()
    if op == "collapse_ws":
        return " ".join(value.split())
    if op in {"parse_typed_id", "parse_mixed_au_id"}:
        result = parse_typed_identifier(
            value, ast["kind"], ast.get("require_label", False)
        )
        return result
    if op == "parse_au_location":
        result = parse_au_location(value, ast["component"])
        return result
    if op == "strip_id_separators":
        if re.search(r"[eE]", value):
            return None  # Never expand scientific notation into a guessed ID.
        return re.sub(r"[\s-]", "", value)
    if op in ("validate_abn", "validate_acn"):
        valid = valid_abn(value) if op == "validate_abn" else valid_acn(value)
        return value if valid else None
    if op in ("parse_date", "parse_datetime"):
        try:
            parsed = (
                datetime.strptime(value, ast["format"])
                if "format" in ast
                else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
            if op == "parse_date":
                result = parsed.date().isoformat()
            else:
                if parsed.tzinfo is None:
                    if ast.get("timezone") != "UTC":
                        raise Failure(
                            "validation_error",
                            "Source datetime lacks timezone",
                            reason_code="datetime_timezone_missing",
                            ast_pointer=pointer or "/",
                        )
                    parsed = parsed.replace(tzinfo=timezone.utc)
                result = parsed.isoformat()
            return result
        except Failure:
            raise
        except (ValueError, TypeError) as exc:
            raise Failure(
                "validation_error",
                "Unparseable date/time",
                reason_code="datetime_parse_error",
                ast_pointer=pointer or "/",
            ) from exc
    raise Failure("validation_error", "Non-value predicate used as transformation")


def canonical_valid(field, value):
    if field not in CANONICAL_FIELDS or not isinstance(value, str) or not value.strip():
        return False
    if field in ENUMS:
        return value in ENUMS[field]
    if field == "entity.abn":
        return valid_abn(value)
    if field == "entity.acn":
        return valid_acn(value)
    if field == "entity.nzbn":
        return bool(re.fullmatch(r"[0-9]{13}", value))
    if field == "entity.date_registered":
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    return True


def _words(value):
    return set(
        re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", value).lower())
    )


def check_utc_assumptions(
    ast,
    evidence,
    documentation,
    inventory,
    diagnostics,
    *,
    pointer,
    mapping_index=None,
    evidence_field=None,
):
    """Walk every compiled node, including conditional and nested transforms."""
    from .semantic_support import timezone_support

    for at, node in ast_nodes(ast):
        if node["op"] != "parse_datetime" or node.get("timezone") != "UTC":
            continue
        fields = {
            child["name"]
            for _, child in ast_nodes(node["input"])
            if child["op"] == "field"
        }
        field = next(iter(fields)) if len(fields) == 1 else None
        support = timezone_support(field, evidence, documentation, inventory)
        if evidence_field is not None and field != evidence_field:
            support.update(
                supported=False,
                reason="Metadata evidence_ref must identify the same timestamp field whose timezone is asserted.",
            )
        if not support["supported"]:
            diagnostics.add(
                "timezone_source_evidence",
                support["reason"],
                pointer + at + "/timezone",
                mapping_index,
                semantic_support=support,
                source_fields=sorted(fields),
            )


def _mapping_semantics(
    mapping,
    index,
    documentation="",
    inventory=(),
    *,
    on_unsupported_conversion=None,
    name_group_value_fields=(),
    reviewed_name_role=False,
):
    from .semantic_support import (
        address_support,
        check_support,
        identifier_support,
        labelled_identifier_support,
        full_address_join_support,
        location_component_support,
        mixed_identifier_support,
        name_role_reviewable,
    )

    target, fields, ast = (
        mapping["canonical_field"],
        mapping["source_fields"],
        mapping["ast"],
    )
    evidence = mapping["semantic_evidence"]
    words = _words(" ".join(fields))
    evidence_words = _words(evidence)
    unknown_abstention = mapping.get("claim_kind") == "unknown_abstention"
    if unknown_abstention and (
        target not in {"entity.entity_type", "entity.status"} or not _only_unknown(ast)
    ):
        raise Failure(
            "validation_error",
            "Declared unknown abstention must statically emit only unknown/omission",
            mapping_index=index,
        )
    support = check_support(
        mapping,
        documentation,
        inventory,
        on_unsupported_conversion=on_unsupported_conversion,
        name_group_value_fields=name_group_value_fields,
    )
    if support and not support["supported"]:
        if reviewed_name_role and name_role_reviewable(support):
            support = {
                "target": target,
                "source_field": mapping["source_field"],
                "supported": True,
                "basis": "independent_evidence_bound_name_review",
                "finite_check": support,
                "limitation": "Model-reviewed semantics, not human gold or ingestion approval.",
            }
        else:
            raise Failure("validation_error", support["reason"], semantic_support=support)
    if target == "address.full":
        if ast["op"] == "join_address":
            support = full_address_join_support(mapping, documentation, inventory)
            if not support["supported"]:
                raise Failure(
                    "validation_error",
                    support["reason"],
                    mapping_index=index,
                    semantic_support=support,
                )
        else:
            support = address_support(mapping, documentation, inventory)
            if not support["supported"]:
                raise Failure(
                    "validation_error",
                    support["reason"],
                    mapping_index=index,
                    semantic_support=support,
                )
    if (
        target == "entity.entity_type"
        and not unknown_abstention
        and "charity" in words
        and not {"legal", "entity", "structure"} & evidence_words
    ):
        raise Failure(
            "validation_error", "Charity label does not establish legal entity type"
        )
    typed = [node for _, node in ast_nodes(ast) if node["op"] == "parse_typed_id"]
    for node in typed:
        if node.get("require_label") is True:
            # Type evidence is checked on every whole raw cell at execution, not
            # inferred from the field's name, another row or identifier length.
            basis = labelled_identifier_support(
                mapping, documentation, node["kind"], inventory
            )
        else:
            basis = identifier_support(mapping, documentation, node["kind"], inventory)
        if (
            target != "entity." + node["kind"]
            or node["input"]["name"]
            != mapping.get("source_field", fields[0] if len(fields) == 1 else None)
            or not basis["supported"]
        ):
            raise Failure(
                "validation_error",
                "Typed identifier needs same-field evidence for its matching canonical type. "
                "Use one exact native header when it explicitly names the required ABN/ACN type, or one exact contiguous same-field definition; "
                "alternatively require_label:true binds the exact primary header or an exact quote from its own identifier definition to a matching whole-cell label and checksum. "
                "A source-level identifier mention without a native-field binding is insufficient; the existing exact primary header remains a valid label-only evidence form. "
                "Never infer type from bare values or length; never fabricate a quote. If still unsupported, remove the mapping and list the source field unmapped.",
                semantic_support=basis,
                exact_primary_header=mapping.get("source_field"),
                identifier_evidence={
                    "reason_code": "identifier_target_or_primary_mismatch"
                    if target != "entity." + node["kind"]
                    or node["input"]["name"] != basis["source_field"]
                    else basis.get("reason_code", "identifier_type_definition_missing"),
                    "source_field": basis["source_field"],
                    "input_field": node["input"]["name"],
                    "canonical_field": target,
                    "parser_kind": node["kind"],
                    "require_label": node.get("require_label", False),
                    "supplied_quote": evidence,
                    "exact_primary_header": basis["source_field"],
                    "accepted_evidence_forms": [
                        "exact_primary_native_header_with_require_label_true",
                        "exact_same_field_identifier_definition",
                    ],
                },
            )
        support = basis
    mixed = [node for _, node in ast_nodes(ast) if node["op"] == "parse_mixed_au_id"]
    for node in mixed:
        basis = mixed_identifier_support(mapping, documentation, inventory)
        if (
            target != "entity." + node["kind"]
            or node["input"]["name"] != basis["source_field"]
            or not basis["supported"]
        ):
            raise Failure(
                "validation_error",
                "Mixed identifier parsing requires exact same-field source evidence explicitly defining an ABN or ACN union. "
                "The complete value must match one allowed identifier grammar and checksum; arbitrary numbers, a different field, AND/composite identifiers or other identifier types are unsupported. "
                "If the original whole cells instead carry explicit ABN/ACN labels, use the existing parse_typed_id with the matching kind, require_label:true and direct primary-field input; "
                "semantic_evidence must be its exact primary native header or an exact quote from its own identifier definition. That route still requires the matching whole-cell label and checksum and omits bare values. "
                "Never infer a type from bare numbers or length, and preserve independently supported mappings.",
                semantic_support=basis,
                exact_primary_header=basis["source_field"],
            )
        support = basis
    for _, node in ast_nodes(ast):
        if node["op"] != "parse_au_location":
            continue
        basis = location_component_support(
            mapping, documentation, inventory, node["component"]
        )
        country_normalization = (
            target == "address.country"
            and node["component"] == "state"
            and ast.get("op") == "enum_lookup"
            and set(ast) == {"op", "input", "values", "default"}
            and ast["input"] is node
            and ast["default"] is None
            and isinstance(ast["values"], dict)
            and bool(ast["values"])
            and set(ast["values"])
            <= {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}
            and all(value == "AU" for value in ast["values"].values())
        )
        if target == "address.country" and not country_normalization:
            basis = {
                **basis,
                "supported": False,
                "target": "address.country",
                "reason_code": "au_country_lookup_shape",
                "allowed_au_state_keys": [
                    "NSW",
                    "VIC",
                    "QLD",
                    "WA",
                    "SA",
                    "TAS",
                    "ACT",
                    "NT",
                ],
                "required_shape": {
                    "root_op": "enum_lookup",
                    "input_op": "parse_au_location",
                    "component": "state",
                    "values": "nonempty subset of allowed_au_state_keys, every value exactly AU",
                    "default": None,
                    "extra_keys": False,
                },
                "reason": "Country normalization requires a root enum_lookup over direct parse_au_location(component:state), a nonempty subset of the eight Australian state keys mapped only to AU, and an explicit null default. Extra entries, including NZ:NZ, are unsupported for this AU-only parser even when unreachable. Keep the same exact source evidence; do not replace it to repair this lookup shape.",
            }
            raise Failure(
                "validation_error",
                basis["reason"],
                semantic_support=basis,
                exact_primary_header=basis["source_field"],
            )
        if (
            (target != "address." + node["component"] and not country_normalization)
            or node["input"]["name"] != basis["source_field"]
            or not basis["supported"]
        ):
            raise Failure("validation_error", basis["reason"], semantic_support=basis)
        if country_normalization:
            basis = {
                **basis,
                "target": "address.country",
                "basis": "same_field_explicit_au_location_state_to_iso_country_normalization",
                "reason": "A complete source-supported location tuple supplies an explicit Australian state; a null-default lookup maps only recognized Australian states to AU.",
                "limitation": "Country normalization is geographic context, not an independently supplied country claim or additional useful field coverage.",
            }
        support = basis
    if target in {"entity.abn", "entity.acn"}:
        typed_required = bool(typed or mixed)
        unchecked = _unchecked_identifier_value_paths(
            ast, target.split(".")[1], typed_required=typed_required
        )
        if unchecked:
            raise Failure(
                "validation_error",
                "Every non-omitting identifier output path must execute its matching typed, mixed or checksum parser. "
                "A parser in a predicate or another branch does not validate this value. If typed/mixed parsing supplies type evidence, an outer checksum cannot replace that value-path obligation; only identifier-preserving normalization may wrap a checked output.",
                semantic_support={
                    "target": target,
                    "source_field": mapping.get("source_field"),
                    "supported": False,
                    "basis": "all_identifier_value_paths_checked",
                    "reason_code": "identifier_value_path_unchecked",
                    "unchecked_ast_paths": unchecked,
                    "requires_typed_value_path": typed_required,
                },
            )
    from .semantic_support import subject_support

    subject = subject_support(
        mapping, documentation, inventory, reviewed_name=reviewed_name_role
    )
    if not subject["supported"]:
        raise Failure("validation_error", subject["reason"], semantic_support=subject)
    support = {**support, "subject_support": subject} if support else subject
    return support


def compile_mappings(
    mappings,
    inventory,
    documentation="",
    unmapped=(),
    *,
    diagnostics=None,
    metadata_fields=(),
    name_group_scope=None,
    reviewed_name_indices=(),
):
    """Collect independent static failures; compile only if every check succeeds."""
    if name_group_scope is not None:
        name_group_scope.clear()
    own = diagnostics is None
    diagnostics = diagnostics or CompileDiagnostics()
    inventory = set(inventory)
    used, roles, compiled, pending = set(metadata_fields), set(), [], []
    if not isinstance(mappings, list):
        diagnostics.add(
            "mapping_list", "field_mappings must be an array", "/field_mappings"
        )
        if own:
            diagnostics.finish()
        return []
    if len(mappings) > 80:
        diagnostics.add(
            "mapping_bound", "Mapping list exceeds bound", "/field_mappings"
        )
    for index, mapping in enumerate(mappings[:80]):
        at = "/field_mappings/" + str(index)

        def issue(code, message, suffix="", **details):
            diagnostics.add(code, message, at + suffix, index, **details)

        if not isinstance(mapping, dict):
            issue("mapping_object", "Mapping must be an object")
            continue
        before = diagnostics.finding_count
        target = mapping.get("canonical_field")
        if not isinstance(target, str) or target not in CANONICAL_FIELDS:
            issue(
                "canonical_field",
                "Unknown canonical field: " + str(target),
                "/canonical_field",
            )
        fields = mapping.get("source_fields")
        valid_fields = (
            isinstance(fields, list)
            and all(isinstance(f, str) for f in fields)
            and len(set(fields)) == len(fields)
        )
        if not valid_fields:
            issue(
                "source_fields_shape",
                "source_fields must be a unique array of field strings",
                "/source_fields",
            )
            fields = []
        used.update(f for f in fields if f in inventory)
        primary = mapping.get("source_field")
        if "source_field" in mapping:
            if not isinstance(primary, str) or primary not in inventory:
                issue(
                    "primary_not_native",
                    "Primary source_field is absent from the native inventory; do not invent ontology/output fields",
                    "/source_field",
                    source_field=primary,
                )
            if primary not in fields:
                issue(
                    "primary_not_declared",
                    "Primary source_field is not in source_fields",
                    "/source_fields",
                    source_field=primary,
                    source_fields=fields,
                )
        confidence = mapping.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            issue("confidence", "Field confidence must be in [0,1]", "/confidence")
        ast = mapping.get("ast")
        ast_pointer = (
            mapping.get("execution_pointer")
            if isinstance(mapping.get("execution_pointer"), str)
            else at + "/ast"
        )
        ast_start = diagnostics.finding_count
        paths = compile_ast(
            ast,
            inventory,
            fields,
            diagnostics=diagnostics,
            pointer=ast_pointer,
            mapping_index=index,
        )
        ast_valid = diagnostics.finding_count == ast_start and valid_fields
        if (
            isinstance(ast, dict)
            and ast.get("op") == "omit"
            and "source_field" in mapping
        ):
            issue(
                "root_omit_no_dependency",
                "A root omit emits nothing and consumes no fields, so it cannot satisfy the required real primary dependency. Remove this no-output mapping from the proposal; keep unsupported source fields explicitly unmapped. Do not add fake dependencies. Conditional omit with a real predicate remains supported.",
            )
        evidence = mapping.get("semantic_evidence", "")
        quote_valid = (
            isinstance(evidence, str)
            and bool(evidence)
            and (evidence in documentation or evidence in inventory)
        )
        if not quote_valid:
            issue(
                "exact_quote",
                "Semantic evidence must be ONE exact contiguous quote from raw documentation or a real header; preserve punctuation and Markdown backticks",
                "/semantic_evidence",
                supplied_quote=evidence,
                exact_header_quotes=[f for f in fields if f in inventory],
                raw_document_lines=[
                    {"line": n, "text": line}
                    for n, line in enumerate(documentation.splitlines(), 1)
                    if any(f and f in line for f in fields)
                ][:8],
            )
        if (
            ast_valid
            and quote_valid
            and isinstance(target, str)
            and target in CANONICAL_FIELDS
        ):
            check_utc_assumptions(
                ast,
                evidence,
                documentation,
                inventory,
                diagnostics,
                pointer=ast_pointer,
                mapping_index=index,
            )
            pending.append(
                (
                    index,
                    mapping,
                    paths,
                    ast_pointer,
                    diagnostics.finding_count == before,
                )
            )

    # Share only structurally checked value dependencies within the same name
    # target. Every mapping still passes its own role, branch and subject checks;
    # a failed peer prevents the whole config from executing.
    from .semantic_support import subject_support

    name_groups = {}
    for index, mapping, _, _, structurally_valid in pending:
        target = mapping["canonical_field"]
        if (
            structurally_valid
            and target in {"entity.legal_name", "entity.trading_name"}
            and subject_support(
                mapping,
                documentation,
                inventory,
                reviewed_name=index in reviewed_name_indices,
            )["supported"]
        ):
            name_groups.setdefault(target, set()).update(
                node["name"]
                for pointer, node in ast_nodes(mapping["ast"])
                if node["op"] == "field" and "/condition/" not in pointer + "/"
            )
    if name_group_scope is not None:
        name_group_scope.update(
            {target: sorted(fields) for target, fields in name_groups.items()}
        )
    for index, mapping, paths, ast_pointer, structurally_valid in pending:
        support = None
        before = diagnostics.finding_count

        def conversion_issue(pointer, conversion_support):
            diagnostics.add(
                "semantic_rule",
                conversion_support["reason"],
                ast_pointer + pointer,
                index,
                semantic_support=conversion_support,
            )

        try:
            support = _mapping_semantics(
                mapping,
                index,
                documentation,
                inventory,
                on_unsupported_conversion=conversion_issue,
                name_group_value_fields=name_groups.get(mapping["canonical_field"], ()),
                reviewed_name_role=index in reviewed_name_indices,
            )
        except Failure as exc:
            if not exc.details.get("semantic_support", {}).get(
                "unsupported_conversion_count"
            ):
                diagnostics.add(
                    "semantic_rule",
                    str(exc),
                    "/field_mappings/" + str(index),
                    index,
                    **{
                        k: v
                        for k, v in exc.details.items()
                        if k not in {"mapping", "mapping_index"}
                    },
                )
        if structurally_valid and diagnostics.finding_count == before:
            compiled.append(
                {
                    "mapping": copy.deepcopy(mapping),
                    "paths": paths,
                    "semantic_support": support,
                }
            )
            subject = support.get("subject_support", support) if support else {}
            roles.update(subject.get("roles", []))
    if len(roles) > 1:
        diagnostics.add(
            "mixed_roles",
            "Mixed subject roles require abstention",
            "/field_mappings",
            roles=sorted(roles),
        )
    if not isinstance(unmapped, list) and not isinstance(unmapped, tuple):
        diagnostics.add(
            "unmapped_shape", "unmapped_fields must be an array", "/unmapped_fields"
        )
        unmapped = []
    unmapped_fields = {
        item["source_field"]
        for item in unmapped
        if isinstance(item, dict)
        and isinstance(item.get("reason"), str)
        and item["reason"].strip()
        and isinstance(item.get("source_field"), str)
    }
    if unmapped_fields - inventory or inventory - used - unmapped_fields:
        diagnostics.add(
            "field_inventory",
            "Inventory contains unexplained or nonexistent fields",
            "/unmapped_fields",
            unexplained=sorted(inventory - used - unmapped_fields),
            nonexistent=sorted(unmapped_fields - inventory),
        )
    if own:
        diagnostics.finish()
    return compiled


def _unchecked_identifier_value_paths(ast, kind, *, typed_required=False, at=""):
    """Prove checksum execution on each output path, never on predicates/peers."""
    op = ast["op"]
    if op == "omit":
        return []
    if op == "validate_" + kind:
        # Preserve the legacy checksum-only contract, but never let its presence
        # erase a typed/mixed mapping's separate value-path type-evidence duty.
        if not typed_required:
            return []
        return _unchecked_identifier_value_paths(
            ast["input"], kind, typed_required=True, at=at + "/input"
        )
    if op in {"parse_typed_id", "parse_mixed_au_id"} and ast["kind"] == kind:
        return []
    if op == "when":
        return [
            path
            for branch in ("then", "else")
            for path in _unchecked_identifier_value_paths(
                ast[branch], kind, typed_required=typed_required, at=at + "/" + branch
            )
        ]
    if op in {"trim", "collapse_ws", "strip_id_separators"}:
        return _unchecked_identifier_value_paths(
            ast["input"], kind, typed_required=typed_required, at=at + "/input"
        )
    return [at or "/"]


def _only_unknown(ast):
    if ast["op"] == "omit":
        return True
    if ast["op"] == "enum_lookup":
        return set(ast["values"].values()) <= {"unknown"} and ast.get("default") in (
            None,
            "unknown",
        )
    if ast["op"] == "when":
        return _only_unknown(ast["then"]) and _only_unknown(ast["else"])
    return False


def extract_records(
    records,
    compiled,
    *,
    licence="unknown",
    observed_time=None,
    dataset_updated=None,
    ingested_at=None,
    source_reliability=0.5,
):
    if not records:
        raise Failure("validation_empty", "No records selected for actual extraction")
    if (
        isinstance(source_reliability, bool)
        or not isinstance(source_reliability, (int, float))
        or not 0 <= source_reliability <= 1
    ):
        raise Failure("validation_error", "Source reliability must be in [0,1]")
    ingested_at = ingested_at or utc_now()
    outputs, errors = [], []
    for record in records:
        raw = record["raw_record"]
        values, confidence, omissions = {}, {}, []
        for index, compiled_mapping in enumerate(compiled):
            mapping = compiled_mapping["mapping"]
            target = mapping["canonical_field"]
            before = {
                field: copy.deepcopy(field_value(raw, field))
                for field in mapping["source_fields"]
            }
            try:
                value = eval_ast(mapping["ast"], raw)
                if value is not None and not canonical_valid(target, value):
                    raise Failure(
                        "validation_error",
                        "Canonical value violates ontology",
                        reason_code="canonical_value_invalid",
                    )
                if value is None:
                    omissions.append(
                        {
                            "canonical_field": target,
                            "reason": "missing_invalid_or_explicitly_omitted",
                            "raw_inputs": before,
                        }
                    )
                elif (
                    target in values
                    and target != "entity.trading_name"
                    and values[target] != value
                ):
                    raise Failure(
                        "validation_error",
                        "Conflicting same-record canonical mappings",
                        reason_code="canonical_mapping_conflict",
                    )
                else:
                    if target == "entity.trading_name":
                        existing = values.setdefault(target, [])
                        if value not in existing:
                            existing.append(value)
                    else:
                        values[target] = value
                    confidence[target] = mapping["confidence"]
            except Failure as exc:
                errors.append(
                    {
                        "source_id": record["source_id"],
                        "source_record_id": record["source_record_id"],
                        "mapping_index": index,
                        "canonical_field": target,
                        "validation_stage": "row_validation",
                        "pointer": mapping.get(
                            "execution_pointer", "/execution/transforms/" + str(index)
                        )
                        + exc.details.get("ast_pointer", "/").rstrip("/"),
                        "error_type": exc.kind,
                        "message": str(exc),
                        "raw_inputs": before,
                        **(
                            {"reason_code": exc.details["reason_code"]}
                            if "reason_code" in exc.details
                            else {}
                        ),
                    }
                )
        limitations = []
        if observed_time is not None:
            time_error = None
            time_inputs = {
                field: copy.deepcopy(field_value(raw, field))
                for field in observed_time["source_fields"]
            }
            try:
                timestamp = eval_ast(observed_time["ast"], raw)
            except Failure as exc:
                timestamp = None
                time_error = exc
            if not timestamp:
                time_error = time_error or Failure(
                    "validation_error",
                    "Missing source-stated time",
                    reason_code="source_time_missing",
                )
                errors.append(
                    {
                        "source_id": record["source_id"],
                        "source_record_id": record["source_record_id"],
                        "metadata_field": "observed_time",
                        "validation_stage": "row_validation",
                        "pointer": "/execution/observed_time/ast"
                        + time_error.details.get("ast_pointer", "/").rstrip("/"),
                        "error_type": time_error.kind,
                        "message": "Source-stated time: " + str(time_error),
                        "raw_inputs": time_inputs,
                        "raw_presence": {
                            field: {
                                key: field_state(raw, field)[key]
                                for key in ("present", "state")
                            }
                            for field in observed_time["source_fields"]
                        },
                        **(
                            {"reason_code": time_error.details["reason_code"]}
                            if "reason_code" in time_error.details
                            else {}
                        ),
                    }
                )
                timestamp = ingested_at
                basis = "ingested_at_proxy"
                time_ref = "runtime.ingested_at"
                limitations.append(
                    "Missing configured source time; ingest proxy used and validation failed"
                )
            else:
                basis, time_ref = "source_stated", observed_time["evidence_ref"]
        elif dataset_updated:
            timestamp, basis, time_ref = (
                dataset_updated,
                "dataset_updated_proxy",
                "catalogue_metadata.updated",
            )
            limitations.append(
                "Dataset update time does not establish individual claim time"
            )
        else:
            timestamp, basis, time_ref = (
                ingested_at,
                "ingested_at_proxy",
                "runtime.ingested_at",
            )
            limitations.append(
                "No usable timezone-aware source-stated or dataset time; ingested_at is an explicit proxy, not a guessed source timezone"
            )
        if licence == "unknown":
            limitations.append("Dataset licence is unknown")
        outputs.append(
            {
                **copy.deepcopy(record),
                "values": values,
                "field_confidence": confidence,
                "source_reliability": source_reliability,
                "observed_at": timestamp,
                "observed_at_basis": basis,
                "observed_at_evidence_ref": time_ref,
                "ingested_at": ingested_at,
                "licence": licence,
                "extractor_version": ENGINE_VERSION,
                "omissions": omissions,
                "limitations": limitations,
            }
        )
    return outputs, {
        "executed_records": len(records),
        "validation_pass": not errors,
        "errors": errors,
        "semantic_support": [
            {"mapping_index": index, **item["semantic_support"]}
            for index, item in enumerate(compiled)
            if item.get("semantic_support")
        ],
        "semantic_check_scope": "finite_field_bound_source_evidence_rules;human_review_required",
    }
