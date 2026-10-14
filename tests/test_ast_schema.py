"""The published AST protocol works in configs and independent rule responses."""

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from firmable_runtime.common import Failure
from firmable_runtime.engine import AST_KEYS, ast_nodes, compile_ast, eval_ast
from firmable_runtime.llm_route import config_schema
from firmable_runtime.schema import (
    CONFIG_SCHEMA,
    ast_rules_schema,
    check_response,
    proposal_schema,
)


def field(name="value"):
    return {"op": "field", "name": name}


def config(ast):
    dependencies = sorted(
        {node["name"] for _, node in ast_nodes(ast) if node["op"] == "field"}
    )
    return {
        "field_mappings": [
            {
                "source_field": dependencies[0] if dependencies else "value",
                "source_fields": dependencies or ["value"],
                "canonical_field": "entity.legal_name",
                "confidence": 0.9,
                "transformation": "Use the declared expression.",
                "semantic_evidence": "value: Company legal name.",
                "execution_pointer": "/execution/transforms/0",
                "validation_pass": False,
            }
        ],
        "unmapped_fields": [],
        "execution": {"transforms": [ast]},
    }


def current_name_expression():
    return {
        "op": "when",
        "condition": {"op": "equals", "input": field("role"), "value": "current"},
        "then": field("recorded_name"),
        "else": {
            "op": "when",
            "condition": {"op": "equals", "input": field("role"), "value": None},
            "then": field("replacement_name"),
            "else": {"op": "omit", "reason": "Unrecognized name role."},
        },
    }


class AstSchemaTests(unittest.TestCase):
    def assert_contract(self, candidate, valid=True):
        rules = {
            "expressions": [
                {"slot": f"mapping:{index}", "ast": ast}
                for index, ast in enumerate(candidate["execution"]["transforms"])
            ]
        }
        for value, schema in (
            (candidate, CONFIG_SCHEMA),
            (candidate, proposal_schema()),
            (rules, ast_rules_schema(len(rules["expressions"]))),
        ):
            if valid:
                self.assertEqual(check_response(value, schema), value)
            else:
                with self.assertRaises(Failure):
                    check_response(value, schema)

    def test_schema_resources_resolve_standalone_and_inside_rule_responses(self):
        for schema in (CONFIG_SCHEMA, proposal_schema(), ast_rules_schema(1)):
            Draft202012Validator.check_schema(schema)
        self.assert_contract(config(current_name_expression()))
        # The task backend shares mapping metadata, but has no AST transforms.
        task = config(field())
        task["execution"] = {
            "kind": "llm_task",
            "task": "Read exact legal-name spans.",
            "subject": "The company described by this record.",
        }
        task["field_mappings"][0]["execution_pointer"] = "/execution/task"
        check_response(
            task,
            config_schema(proposal_schema()["properties"]["field_mappings"]["items"]),
        )

    def test_all_published_operations_match_compiler_keys_and_compile(self):
        examples = {
            "field": field(),
            **{
                op: {"op": op, "input": field()}
                for op in (
                    "trim",
                    "collapse_ws",
                    "strip_id_separators",
                    "validate_abn",
                    "validate_acn",
                )
            },
            "parse_typed_id": {
                "op": "parse_typed_id",
                "input": field(),
                "kind": "abn",
                "require_label": True,
            },
            "parse_mixed_au_id": {
                "op": "parse_mixed_au_id",
                "input": field(),
                "kind": "acn",
            },
            "parse_au_location": {
                "op": "parse_au_location",
                "input": field(),
                "component": "postcode",
            },
            "parse_date": {"op": "parse_date", "input": field(), "format": "%d/%m/%Y"},
            "parse_datetime": {
                "op": "parse_datetime",
                "input": field(),
                "timezone": "UTC",
            },
            "enum_lookup": {
                "op": "enum_lookup",
                "input": field(),
                "values": {"closed": "deregistered"},
                "default": None,
            },
            "join_address": {
                "op": "join_address",
                "inputs": [field(), field("other")],
                "require_all": True,
                "on_missing": "omit",
            },
            "omit": {"op": "omit", "reason": "No supported value."},
            "when": current_name_expression(),
            "is_present": {"op": "is_present", "input": field()},
            "equals": {"op": "equals", "input": field(), "value": None},
            "in": {"op": "in", "input": field(), "values": [None, "present"]},
        }
        self.assertEqual(set(examples), set(AST_KEYS))
        for op, expression in examples.items():
            with self.subTest(op=op):
                self.assertEqual(
                    set(CONFIG_SCHEMA["$defs"][op]["properties"]), AST_KEYS[op]
                )
                if op in {"is_present", "equals", "in"}:
                    expression = {
                        "op": "when",
                        "condition": expression,
                        "then": field(),
                        "else": {"op": "omit", "reason": "Predicate false."},
                    }
                candidate = config(expression)
                self.assert_contract(candidate)
                dependencies = {
                    node["name"]
                    for _, node in ast_nodes(expression)
                    if node["op"] == "field"
                }
                compile_ast(expression, dependencies, dependencies)

    def test_conditional_current_names_keep_both_branches_and_all_dependencies(self):
        expression = current_name_expression()
        self.assert_contract(config(expression))
        dependencies = {"role", "recorded_name", "replacement_name"}
        compile_ast(expression, dependencies, dependencies)
        rows = [
            (
                {
                    "role": "current",
                    "recorded_name": "Oak Limited",
                    "replacement_name": None,
                },
                "Oak Limited",
            ),
            (
                {
                    "role": None,
                    "recorded_name": "Old Elm Limited",
                    "replacement_name": "Elm Limited",
                },
                "Elm Limited",
            ),
            (
                {
                    "role": None,
                    "recorded_name": "Old Elm Limited",
                    "replacement_name": None,
                },
                None,
            ),
            (
                {
                    "role": "unrecognized",
                    "recorded_name": "Birch Limited",
                    "replacement_name": "Other Limited",
                },
                None,
            ),
        ]
        for raw, expected in rows:
            self.assertEqual(eval_ast(expression, raw), expected)
        for missing in dependencies:
            with self.subTest(undeclared=missing), self.assertRaises(Failure):
                compile_ast(expression, dependencies, dependencies - {missing})
        ordinary = {"op": "trim", "input": field()}
        self.assert_contract(config(ordinary))
        self.assertEqual(
            eval_ast(ordinary, {"value": "  Ordinary Limited  "}), "Ordinary Limited"
        )

    def test_bad_ast_shapes_are_rejected_before_execution_in_all_contexts(self):
        valid = current_name_expression()
        wrong_selector = copy.deepcopy(valid)
        wrong_selector["when"] = wrong_selector.pop("condition")
        wrong_predicate = copy.deepcopy(valid)
        wrong_predicate["condition"] = field("role")
        wrong_branch = copy.deepcopy(valid)
        wrong_branch["then"] = 0
        extra_predicate_key = copy.deepcopy(valid)
        extra_predicate_key["condition"]["field"] = "role"
        missing_predicate_value = copy.deepcopy(valid)
        del missing_predicate_value["condition"]["value"]
        malformed = [
            wrong_selector,
            wrong_predicate,
            wrong_branch,
            extra_predicate_key,
            missing_predicate_value,
            {"op": "trim", "input": 0},
            {"op": "trim", "input": {"op": "field", "name": "value", "alias": "value"}},
            {"op": "omit", "reason": "  "},
            {
                "op": "parse_typed_id",
                "input": {"op": "trim", "input": field()},
                "kind": "abn",
            },
            {"op": "parse_au_location", "input": field(), "component": "street"},
            {"op": "parse_date", "input": field(), "format": "%Z"},
            {
                "op": "enum_lookup",
                "input": field(),
                "values": {"A": "active"},
                "default": "active",
            },
            {
                "op": "join_address",
                "inputs": [field(), field("other")],
                "require_all": False,
                "on_missing": "omit",
            },
            {
                "op": "when",
                "condition": {"op": "in", "input": field(), "values": "current"},
                "then": field(),
                "else": field(),
            },
        ]
        for expression in malformed:
            with self.subTest(expression=expression):
                candidate = config(field())
                candidate["execution"]["transforms"] = [expression]
                self.assert_contract(candidate, valid=False)

    def test_recursive_schema_does_not_weaken_compiler_depth_bound(self):
        expression = field()
        for _ in range(17):
            expression = {"op": "validate_acn", "input": expression}
        self.assert_contract(config(expression))
        with self.assertRaises(Failure):
            compile_ast(expression, {"value"}, {"value"})

    def test_six_selected_r2_configs_satisfy_published_contract(self):
        root = Path(__file__).resolve().parents[1] / "outputs" / "configs"
        paths = sorted(root.glob("real-*.json"))
        self.assertEqual(len(paths), 6)
        for path in paths:
            with self.subTest(source=path.stem):
                check_response(json.loads(path.read_text()), CONFIG_SCHEMA)


if __name__ == "__main__":
    unittest.main()
