"""Lossless strict transport, with the original host validator/compiler retained."""

import copy
import unittest

from firmable_runtime.agent import decision_schema
from firmable_runtime.common import Failure
from firmable_runtime.config_audit import review_schema
from firmable_runtime.engine import AST_KEYS, ast_nodes, compile_ast, eval_ast
from firmable_runtime.execution import compile_config
from firmable_runtime.llm_route import extraction_schema, VERIFY_SCHEMA
from firmable_runtime.openrouter_wire import OpenRouterWireContract
from firmable_runtime.schema import CONFIG_SCHEMA, INSPECT_SCHEMA, ast_rules_schema
from test_ast_schema import config, current_name_expression, field


def wire_scalar(value):
    kind = {
        str: "string",
        int: "number",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }[type(value)]
    return {
        "kind": kind,
        "string_value": value if kind == "string" else "",
        "number_value": value if kind == "number" else 0,
        "boolean_value": value if kind == "boolean" else False,
    }


def wire_node(op, *, inputs=None, enum_entries=None, in_values=None, **arguments):
    return {
        "op": op,
        "arguments": [
            {"key": key, "value": wire_scalar(value)}
            for key, value in arguments.items()
        ],
        "inputs": inputs or [],
        "enum_entries": enum_entries or [],
        "in_values": [wire_scalar(value) for value in (in_values or [])],
    }


class WireTests(unittest.TestCase):
    def test_independent_rules_keep_explicit_mapping_and_time_slots_without_unions(
        self,
    ):
        expressions = [{"slot": "mapping:0", "ast": current_name_expression()}]
        for name in ("observed_time", "valid_from", "valid_to"):
            expressions.append(
                {
                    "slot": "time:" + name,
                    "ast": {"op": "parse_datetime", "input": field(name)},
                }
            )
        host = {"expressions": list(reversed(expressions))}
        contract = OpenRouterWireContract(ast_rules_schema(4))
        self.assertEqual(
            contract.complexity, {"optional_parameters": 0, "union_parameters": 0}
        )
        self.assertFalse(contract.wrapped_root)
        encoded = contract.encode(host)
        self.assertEqual(
            [item["slot"] for item in encoded["expressions"]],
            [item["slot"] for item in host["expressions"]],
        )
        self.assertIn("nodes", encoded["expressions"][0]["ast"])
        restored = contract.decode(encoded)
        self.assertEqual(restored, host)
        for item in restored["expressions"]:
            fields = {
                node["name"]
                for _, node in ast_nodes(item["ast"])
                if node["op"] == "field"
            }
            compile_ast(item["ast"], fields, fields)
        for bad in (
            {"expressions": encoded["expressions"][:3]},
            {"expressions": [*encoded["expressions"], encoded["expressions"][0]]},
        ):
            with (
                self.subTest(count=len(bad["expressions"])),
                self.assertRaises(Failure),
            ):
                contract.decode(bad)
        encoded["expressions"][0]["slot"] = "time:invented"
        with self.assertRaises(Failure):
            contract.decode(encoded)

    def test_rule_count_is_bounded_and_zero_rules_are_explicit(self):
        for count in (True, -1, 84, 1.5, "1", None):
            with self.subTest(count=count), self.assertRaises(Failure):
                ast_rules_schema(count)
        empty = {"expressions": []}
        contract = OpenRouterWireContract(ast_rules_schema(0))
        self.assertEqual(contract.decode(contract.encode(empty)), empty)
        largest = OpenRouterWireContract(ast_rules_schema(83))
        self.assertEqual(
            largest.complexity, {"optional_parameters": 0, "union_parameters": 0}
        )

    def test_tagged_literals_preserve_all_scalar_types_without_schema_unions(self):
        contract = OpenRouterWireContract(CONFIG_SCHEMA)

        def check_fixed(schema):
            if isinstance(schema, dict):
                self.assertNotIn("anyOf", schema)
                self.assertNotIsInstance(schema.get("type"), list)
                for item in schema.values():
                    check_fixed(item)
            elif isinstance(schema, list):
                for item in schema:
                    check_fixed(item)

        check_fixed(contract.schema["$defs"]["flat_ast"])
        for value in (None, True, False, "", "label", 0, -7, 0.0, 1.5):
            with self.subTest(value=value, type=type(value)):
                ast = current_name_expression()
                ast["condition"]["value"] = value
                host = config(ast)
                restored = contract.decode(contract.encode(host))
                actual = restored["execution"]["transforms"][0]["condition"]["value"]
                self.assertEqual(type(actual), type(value))
                self.assertEqual(actual, value)

    def test_inactive_tagged_slots_cannot_smuggle_values(self):
        contract = OpenRouterWireContract(CONFIG_SCHEMA)
        for value in (None, True, "label", 1.5):
            for slot, nonneutral in (
                ("string_value", "hidden"),
                ("number_value", 7),
                ("boolean_value", True),
            ):
                with self.subTest(value=value, slot=slot):
                    ast = current_name_expression()
                    ast["condition"]["value"] = value
                    encoded = contract.encode(config(ast))
                    predicate = next(
                        node
                        for node in encoded["execution"]["transforms"][0]["nodes"]
                        if node["op"] == "equals"
                    )
                    tagged = next(
                        item["value"]
                        for item in predicate["arguments"]
                        if item["key"] == "value"
                    )
                    if slot == tagged["kind"] + "_value":
                        continue
                    tagged[slot] = nonneutral
                    with self.assertRaises(Failure) as caught:
                        contract.decode(encoded)
                    self.assertEqual(caught.exception.details["rule"], "scalar_neutral")
        ast = current_name_expression()
        ast["condition"] = {"op": "in", "input": field("role"), "values": [None]}
        encoded = contract.encode(config(ast))
        predicate = next(
            node
            for node in encoded["execution"]["transforms"][0]["nodes"]
            if node["op"] == "in"
        )
        predicate["in_values"][0]["boolean_value"] = True
        with self.assertRaises(Failure) as caught:
            contract.decode(encoded)
        self.assertEqual(caught.exception.details["rule"], "scalar_neutral")

    def test_all_eighteen_operations_roundtrip_through_fixed_nodes(self):
        expressions = {
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
                "component": "state",
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
                "values": {"A": "active"},
                "default": None,
            },
            "join_address": {
                "op": "join_address",
                "inputs": [field(), field("other")],
                "require_all": True,
                "on_missing": "omit",
                "separator": ", ",
            },
            "omit": {"op": "omit", "reason": "Unavailable."},
            "when": current_name_expression(),
            "is_present": {"op": "is_present", "input": field()},
            "equals": {"op": "equals", "input": field(), "value": False},
            "in": {"op": "in", "input": field(), "values": [None, True, 1, 1.5, "x"]},
        }
        self.assertEqual(set(expressions), set(AST_KEYS))
        contract = OpenRouterWireContract(CONFIG_SCHEMA)
        node_schema = contract.schema["$defs"]["flat_ast"]["properties"]["nodes"][
            "items"
        ]
        self.assertNotIn("anyOf", node_schema)
        self.assertEqual(set(node_schema["properties"]), set(node_schema["required"]))
        for op, expression in expressions.items():
            with self.subTest(op=op):
                if op in {"is_present", "equals", "in"}:
                    expression = {
                        "op": "when",
                        "condition": expression,
                        "then": field(),
                        "else": {"op": "omit", "reason": "False."},
                    }
                host = config(expression)
                wire = contract.encode(host)
                restored = contract.decode(wire)
                self.assertEqual(restored, host)
                deps = {
                    node["name"]
                    for _, node in ast_nodes(expression)
                    if node["op"] == "field"
                }
                compile_ast(restored["execution"]["transforms"][0], deps, deps)

    def test_duplicate_wrong_op_unknown_and_unused_node_attributes_fail_closed(self):
        contract = OpenRouterWireContract(CONFIG_SCHEMA)
        good = contract.encode(config({"op": "trim", "input": field()}))
        invalid_nodes = [
            wire_node("trim", input=0, name="not allowed by trim"),
            wire_node("trim", input=0, undocumented="unknown"),
            wire_node("trim", input=0, inputs=[0]),
            wire_node("trim", input=0, enum_entries=[{"key": "A", "value": "active"}]),
            wire_node("trim", input=0, in_values=["A"]),
            wire_node("trim"),
            wire_node("parse_typed_id", input=0, kind="invented type"),
        ]
        duplicated = wire_node("when", condition=0, then=0)
        duplicated["arguments"].append({"key": "then", "value": wire_scalar(0)})
        invalid_nodes.append(duplicated)
        for node in invalid_nodes:
            with self.subTest(node=node), self.assertRaises(Failure):
                encoded = copy.deepcopy(good)
                encoded["execution"]["transforms"][0]["nodes"][-1] = node
                contract.decode(encoded)

    def test_every_runtime_response_contract_fits_documented_complexity_limits(self):
        for schema in (
            INSPECT_SCHEMA,
            decision_schema(),
            ast_rules_schema(83),
            review_schema([0, 1]),
            extraction_schema(),
            VERIFY_SCHEMA,
        ):
            with self.subTest(schema=list(schema)):
                wire = OpenRouterWireContract(schema)
                self.assertLessEqual(wire.complexity["optional_parameters"], 24)
                self.assertLessEqual(wire.complexity["union_parameters"], 16)

    def test_conditional_roundtrip_keeps_both_outputs_and_dependencies(self):
        ast = current_name_expression()
        host = config(ast)
        wire = OpenRouterWireContract(CONFIG_SCHEMA)
        encoded = wire.encode(host)
        nodes = encoded["execution"]["transforms"][0]["nodes"]
        self.assertIsInstance(
            {
                item["key"]: item["value"]["number_value"]
                for item in nodes[-1]["arguments"]
            }["condition"],
            int,
        )
        self.assertEqual(wire.decode(encoded), host)
        restored = wire.decode(encoded)["execution"]["transforms"][0]
        compile_ast(
            restored,
            {"role", "recorded_name", "replacement_name"},
            {"role", "recorded_name", "replacement_name"},
        )
        self.assertEqual(
            eval_ast(restored, {"role": "current", "recorded_name": "Now"}), "Now"
        )
        self.assertEqual(
            eval_ast(
                restored,
                {"role": None, "recorded_name": "Then", "replacement_name": "Now"},
            ),
            "Now",
        )
        self.assertIsNone(eval_ast(restored, {"role": None, "recorded_name": "Then"}))

    def test_root_union_uses_closed_object_with_definitions_outside_anyof(self):
        contract = OpenRouterWireContract(
            {
                "oneOf": [
                    ast_rules_schema(1),
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["status", "reason"],
                        "properties": {
                            "status": {"const": "stop"},
                            "reason": {"type": "string"},
                        },
                    },
                ]
            }
        )
        self.assertEqual(contract.schema["type"], "object")
        self.assertFalse(contract.schema["additionalProperties"])
        self.assertEqual(contract.schema["required"], ["result"])
        self.assertEqual(set(contract.schema["properties"]), {"result"})
        self.assertIn("flat_ast", contract.schema["$defs"])
        union = contract.schema["properties"]["result"]
        self.assertIn("anyOf", union)
        self.assertNotIn("$defs", union)
        host = {
            "expressions": [{"slot": "mapping:0", "ast": current_name_expression()}]
        }
        encoded = contract.encode(host)
        self.assertEqual(set(encoded), {"result"})
        self.assertIsInstance(encoded["result"]["expressions"][0]["ast"]["nodes"], list)
        self.assertEqual(contract.decode(encoded), host)
        for invalid in (
            host,
            {**encoded, "extra": 1},
            {"result": "{}"},
            {"result": {"action": "stop"}},
        ):
            with self.subTest(value=invalid), self.assertRaises(Failure):
                contract.decode(invalid)
        stop = {"status": "stop", "reason": "Protocol test only."}
        self.assertEqual(contract.decode(contract.encode(stop)), stop)
        object_contract = OpenRouterWireContract(INSPECT_SCHEMA)
        direct = {"selected_table_ids": [0], "reason": "The selected native table."}
        self.assertEqual(object_contract.encode(direct), direct)
        self.assertEqual(object_contract.decode(direct), direct)

    def test_enum_pairs_roundtrip_and_duplicate_keys_are_rejected(self):
        host = config(
            {
                "op": "enum_lookup",
                "input": field(),
                "values": {"A": "active", "D": "deregistered"},
                "default": None,
            }
        )
        wire = OpenRouterWireContract(CONFIG_SCHEMA)
        encoded = wire.encode(host)
        pairs = encoded["execution"]["transforms"][0]["nodes"][-1]["enum_entries"]
        self.assertEqual(
            pairs,
            [{"key": "A", "value": "active"}, {"key": "D", "value": "deregistered"}],
        )
        pairs.append({"key": "A", "value": "suspended"})
        with self.assertRaises(Failure):
            wire.decode(encoded)

    def test_forward_self_negative_and_unreachable_nodes_are_rejected(self):
        wire = OpenRouterWireContract(CONFIG_SCHEMA)
        host = config({"op": "trim", "input": field()})
        for ref in (-1, 1, 2, True, 0.0, 0.5):
            with self.subTest(ref=ref):
                encoded = wire.encode(host)
                encoded["execution"]["transforms"][0]["nodes"][-1]["arguments"][0][
                    "value"
                ] = wire_scalar(ref)
                with self.assertRaises(Failure) as caught:
                    wire.decode(encoded)
                self.assertEqual(caught.exception.details["rule"], "ast_reference")
        encoded = wire.encode(host)
        encoded["execution"]["transforms"][0]["nodes"].append(
            wire_node("field", name="unreachable-other-root")
        )
        with self.assertRaises(Failure):
            wire.decode(encoded)

    def test_node_count_depth_and_expansion_are_bounded_before_materialization(self):
        wire = OpenRouterWireContract(CONFIG_SCHEMA)
        encoded = wire.encode(config(field()))
        for nodes in (
            [wire_node("field", name="value")] * 129,
            [wire_node("field", name="value")]
            + [wire_node("trim", input=i) for i in range(17)],
            [wire_node("field", name="value")]
            + [
                wire_node(
                    "join_address", inputs=[i, i], require_all=True, on_missing="omit"
                )
                for i in range(8)
            ],
        ):
            with self.subTest(nodes=len(nodes)):
                encoded["execution"]["transforms"][0]["nodes"] = nodes
                with self.assertRaises(Failure):
                    wire.decode(encoded)

    def test_wire_compatible_shape_still_obeys_original_ast_rules(self):
        wire = OpenRouterWireContract(CONFIG_SCHEMA)
        encoded = wire.encode(config({"op": "trim", "input": field()}))
        # A typed-ID parser requires a direct field, not an arbitrary expression.
        encoded["execution"]["transforms"][0]["nodes"].append(
            wire_node("parse_typed_id", input=1, kind="abn")
        )
        with self.assertRaises(Failure):
            wire.decode(encoded)

    def test_unknown_schema_features_and_unencoded_recursion_fail_closed(self):
        schemas = [
            {
                "type": "object",
                "properties": {"child": {"$ref": "#/definitions/node"}},
                "definitions": {
                    "node": {
                        "type": "object",
                        "properties": {"child": {"$ref": "#/definitions/node"}},
                    }
                },
            },
            {"type": "string", "contentEncoding": "base64"},
            {
                "type": "object",
                "properties": {str(i): {"type": "string"} for i in range(25)},
            },
            {
                "type": "object",
                "required": [str(i) for i in range(17)],
                "properties": {str(i): {"type": ["string", "null"]} for i in range(17)},
            },
        ]
        for schema in schemas:
            with self.subTest(schema=list(schema)), self.assertRaises(Failure):
                OpenRouterWireContract(schema)

    def test_wire_compilation_and_conversion_do_not_modify_inputs(self):
        schema, host = (
            ast_rules_schema(1),
            {"expressions": [{"slot": "mapping:0", "ast": current_name_expression()}]},
        )
        original = copy.deepcopy((schema, host))
        wire = OpenRouterWireContract(schema)
        encoded = wire.encode(host)
        before = copy.deepcopy(encoded)
        wire.decode(encoded)
        self.assertEqual((schema, host), original)
        self.assertEqual(encoded, before)

    def test_time_metadata_roundtrip_retains_schema_and_compiler_guards(self):
        slots = {
            "observed_time": "observed",
            "valid_from": "starts",
            "valid_to": "ends",
        }
        host = {
            "field_mappings": [],
            "unmapped_fields": [],
            "execution": {"transforms": []},
        }
        for slot, name in slots.items():
            host["execution"][slot] = {
                "ast": {"op": "parse_datetime", "input": field(name)},
                "source_fields": [name],
                "evidence_ref": name,
            }
        contract = OpenRouterWireContract(CONFIG_SCHEMA)
        wire = contract.encode(host)
        restored = contract.decode(wire)
        self.assertEqual(restored, host)
        for slot, name in slots.items():
            self.assertEqual(
                wire["execution"][slot]["ast"]["nodes"][-1]["arguments"][0]["value"],
                wire_scalar(0),
            )
            compile_ast(restored["execution"][slot]["ast"], set(slots.values()), [name])
            self.assertEqual(
                eval_ast(
                    restored["execution"][slot]["ast"], {name: "2026-10-13T00:00:00Z"}
                ),
                "2026-10-13T00:00:00+00:00",
            )
        restored["source_schema"] = {
            "format": "json",
            "fields": [
                {"source_field": name, "inferred_type": "text"}
                for name in slots.values()
            ],
            "data_files": ["records.json"],
            "discovery_method": "test native inventory",
        }
        docs = "observed: Record timestamp.\nstarts: Validity start date for the record.\nends: Validity end date for the record."
        compile_config(restored, docs)
        broken = copy.deepcopy(restored)
        broken["execution"]["observed_time"]["evidence_ref"] = "starts"
        with self.assertRaises(Failure) as caught:
            compile_config(broken, docs)
        self.assertIn(
            "time_evidence",
            {item["code"] for item in caught.exception.details["findings"]},
        )
        wire["execution"]["observed_time"]["source_fields"] = [7]
        with self.assertRaises(Failure):
            contract.decode(wire)
        host["execution"]["observed_time"] = None
        self.assertEqual(contract.decode(contract.encode(host)), host)

    def test_host_owned_source_schema_is_not_model_output(self):
        contract = OpenRouterWireContract(CONFIG_SCHEMA)
        host = config(field())
        host["source_schema"] = {"reader_specs": [{"file": "records.json"}]}
        with self.assertRaises(Failure):
            contract.encode(host)
        self.assertIn("Omit source_schema", contract.instructions)


if __name__ == "__main__":
    unittest.main()
