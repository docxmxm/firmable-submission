"""One response contract, validated with the standard JSON Schema implementation."""

import copy
import json


def _ast_node(op, properties, required):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", *required],
        "properties": {"op": {"const": op}, **properties},
    }


_EXPRESSION = {"$ref": "#/$defs/expression"}
_FIELD = {"$ref": "#/$defs/field"}
_DATE_FORMAT = {"type": "string", "maxLength": 60, "not": {"pattern": "%Z"}}
_AST_DEFS = {
    "field": _ast_node("field", {"name": {"type": "string", "minLength": 1}}, ["name"]),
    **{
        op: _ast_node(op, {"input": _EXPRESSION}, ["input"])
        for op in (
            "trim",
            "collapse_ws",
            "strip_id_separators",
            "validate_abn",
            "validate_acn",
        )
    },
    "parse_typed_id": _ast_node(
        "parse_typed_id",
        {
            "input": _FIELD,
            "kind": {"enum": ["abn", "acn"]},
            "require_label": {"type": "boolean"},
        },
        ["input", "kind"],
    ),
    "parse_mixed_au_id": _ast_node(
        "parse_mixed_au_id",
        {"input": _FIELD, "kind": {"enum": ["abn", "acn"]}},
        ["input", "kind"],
    ),
    "parse_au_location": _ast_node(
        "parse_au_location",
        {"input": _FIELD, "component": {"enum": ["locality", "state", "postcode"]}},
        ["input", "component"],
    ),
    "parse_date": _ast_node(
        "parse_date", {"input": _EXPRESSION, "format": _DATE_FORMAT}, ["input"]
    ),
    "parse_datetime": _ast_node(
        "parse_datetime",
        {"input": _EXPRESSION, "format": _DATE_FORMAT, "timezone": {"const": "UTC"}},
        ["input"],
    ),
    "enum_lookup": _ast_node(
        "enum_lookup",
        {
            "input": _EXPRESSION,
            "values": {
                "type": "object",
                "maxProperties": 100,
                "additionalProperties": {"type": "string"},
            },
            "default": {"enum": [None, "unknown"]},
        },
        ["input", "values"],
    ),
    "join_address": _ast_node(
        "join_address",
        {
            "inputs": {
                "type": "array",
                "minItems": 2,
                "maxItems": 12,
                "items": _EXPRESSION,
            },
            "require_all": {"const": True},
            "on_missing": {"const": "omit"},
            "separator": {"type": "string", "maxLength": 8},
        },
        ["inputs", "require_all", "on_missing"],
    ),
    "omit": _ast_node(
        "omit", {"reason": {"type": "string", "pattern": "\\S"}}, ["reason"]
    ),
    "when": _ast_node(
        "when",
        {
            "condition": {"$ref": "#/$defs/predicate"},
            "then": _EXPRESSION,
            "else": _EXPRESSION,
        },
        ["condition", "then", "else"],
    ),
    "is_present": _ast_node("is_present", {"input": _EXPRESSION}, ["input"]),
    "equals": _ast_node(
        "equals", {"input": _EXPRESSION, "value": {}}, ["input", "value"]
    ),
    "in": _ast_node(
        "in",
        {"input": _EXPRESSION, "values": {"type": "array", "maxItems": 100}},
        ["input", "values"],
    ),
}
_PREDICATES = {"is_present", "equals", "in"}


def _ast_union(operations):
    # Dispatch before descending: a recursive oneOf would also traverse inputs
    # of mismatched operations, doing exponential work on deeply nested trees.
    return {
        "type": "object",
        "required": ["op"],
        "properties": {"op": {"enum": list(operations)}},
        "allOf": [
            {
                "if": {"properties": {"op": {"const": op}}},
                "then": {"$ref": "#/$defs/" + op},
            }
            for op in operations
        ],
    }


_AST_DEFS["expression"] = _ast_union([op for op in _AST_DEFS if op not in _PREDICATES])
_AST_DEFS["predicate"] = _ast_union(sorted(_PREDICATES))

_TIME_SPEC = {
    "anyOf": [
        {
            "type": "object",
            "required": ["ast", "source_fields", "evidence_ref"],
            "properties": {
                "ast": _EXPRESSION,
                "source_fields": {"type": "array", "items": {"type": "string"}},
                "evidence_ref": {"type": "string"},
            },
        },
        {"type": "null"},
    ]
}

CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    # Keep fragment references local when the config is embedded in a planner
    # decision schema. Without this resource boundary, they target that wrapper.
    "$id": "urn:firmable:config:bounded-ast-0.4",
    "$defs": _AST_DEFS,
    "type": "object",
    "required": ["field_mappings", "unmapped_fields", "execution"],
    "properties": {
        "field_mappings": {
            "type": "array",
            "maxItems": 80,
            "items": {
                "type": "object",
                "required": [
                    "source_field",
                    "source_fields",
                    "canonical_field",
                    "confidence",
                    "transformation",
                    "semantic_evidence",
                    "execution_pointer",
                    "validation_pass",
                ],
                "properties": {
                    "source_field": {"type": "string", "minLength": 1},
                    "source_fields": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "canonical_field": {
                        "enum": [
                            "address.country",
                            "address.full",
                            "address.locality",
                            "address.postcode",
                            "address.state",
                            "entity.abn",
                            "entity.acn",
                            "entity.date_registered",
                            "entity.entity_type",
                            "entity.industry_code",
                            "entity.legal_name",
                            "entity.nzbn",
                            "entity.status",
                            "entity.trading_name",
                            "entity.website",
                        ]
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "transformation": {"type": "string"},
                    "semantic_evidence": {"type": "string", "minLength": 1},
                    "claim_kind": {"enum": ["informative", "unknown_abstention"]},
                    "execution_pointer": {
                        "type": "string",
                        "pattern": "^/execution/transforms/[0-9]+$",
                    },
                    "validation_pass": {"type": "boolean"},
                },
            },
        },
        "unmapped_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_field", "reason"],
                "properties": {
                    "source_field": {"type": "string"},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "execution": {
            "type": "object",
            "required": ["transforms"],
            "properties": {
                "kind": {"const": "ast"},
                "transforms": {"type": "array", "maxItems": 80, "items": _EXPRESSION},
                **{
                    name: _TIME_SPEC
                    for name in ("observed_time", "valid_from", "valid_to")
                },
            },
        },
    },
}

AST_RULES_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:firmable:ast-rules-response:0.1",
    "$defs": _AST_DEFS,
    "type": "object",
    "additionalProperties": False,
    "required": ["expressions"],
    "properties": {
        "expressions": {
            "type": "array",
            "maxItems": 83,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "ast"],
                "properties": {
                    "slot": {
                        "type": "string",
                        "pattern": "^(mapping:[0-9]+|time:(observed_time|valid_from|valid_to))$",
                    },
                    "ast": _EXPRESSION,
                },
            },
        }
    },
}


def ast_rules_schema(count):
    """One named expression per mapping/time slot; assembly checks slot coverage."""
    from .common import Failure

    if type(count) is not int or not 0 <= count <= 83:
        raise Failure(
            "validation_error", "AST rule count must be an integer from 0 to 83"
        )
    schema = copy.deepcopy(AST_RULES_SCHEMA)
    schema["properties"]["expressions"].update(minItems=count, maxItems=count)
    return schema


INSPECT_SCHEMA = {
    "type": "object",
    "required": ["selected_table_ids", "reason"],
    "additionalProperties": False,
    "properties": {
        "selected_table_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 0},
        },
        "reason": {"type": "string"},
    },
}


def proposal_schema():
    """A model cannot assert that its own proposal has already passed validation."""
    schema = copy.deepcopy(CONFIG_SCHEMA)
    schema["properties"]["field_mappings"]["items"]["properties"]["validation_pass"] = {
        "const": False
    }
    return schema


def check_response(value, schema, mode="direct"):
    from jsonschema import Draft202012Validator
    from .common import Failure, strict_json

    if mode == "wrapped":
        if (
            not isinstance(value, dict)
            or set(value) != {"response_json"}
            or not isinstance(value["response_json"], str)
        ):
            raise Failure("validation_error", "Expected response_json string envelope")
        value = strict_json(value["response_json"].encode())
    # Round-trip rejects non-finite values even when a test provider supplies objects.
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise Failure("validation_error", "Response must be finite JSON") from exc
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        raise Failure(
            "validation_error",
            "Response does not satisfy schema",
            path=list(error.absolute_path),
            rule=error.validator,
        )
    return value


SOURCE_SCHEMA = {
    "type": "object",
    "required": ["format", "fields", "data_files", "discovery_method"],
    "properties": {
        "format": {"type": "string", "enum": ["csv", "json", "excel", "tsv", "other"]},
        "encoding": {"type": "string"},
        "record_path": {
            "type": "string",
            "description": "For nested JSON, path to record array",
        },
        "sheet_name": {
            "type": "string",
            "description": "For Excel, which sheet contains entities",
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_field", "inferred_type"],
                "properties": {
                    "source_field": {"type": "string"},
                    "sample_values": {"type": "array", "items": {"type": "string"}},
                    "inferred_type": {"type": "string"},
                },
            },
        },
        "discovery_method": {"type": "string", "minLength": 1},
        "discovered_data_sheet": {"type": ["string", "null"]},
        "header_row_index": {"type": ["integer", "null"], "minimum": 0},
        "data_files": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
            "description": "Discovered native files, relative to evidence_root; no path "
            "hints in request.",
        },
        "delimiter": {"type": ["string", "null"]},
    },
}
