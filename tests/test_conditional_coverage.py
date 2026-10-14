"""Conditional name obligations must distinguish business rules from empty guards.

Scripted reviews exercise the host contract, not model accuracy. The positive
available-on-empty cases retain the regression that first reproduced the defect.
"""

import copy
import unittest

from firmable_runtime.common import Failure, digest
from firmable_runtime.config_audit import (
    audit_name_mappings,
    validated_name_review,
)
from firmable_runtime.engine import eval_ast


def field(name):
    return {"op": "field", "name": name}


def normalize(name, operations=("trim",)):
    expression = field(name)
    for operation in operations:
        expression = {"op": operation, "input": expression}
    return expression


def empty_guard(name="n24", operations=("trim",)):
    return {
        "op": "when",
        "condition": {"op": "is_present", "input": field(name)},
        "then": normalize(name, operations),
        "else": {"op": "omit", "reason": "No native value"},
    }


def configuration(expression, fields=("n24",), docs=None, dependencies=None):
    docs = docs or "n24: Current registered legal name of the subject business."
    return {
        "source_schema": {
            "format": "json",
            "data_files": ["invented.json"],
            "discovery_method": "synthetic regression fixture",
            "fields": [
                {"source_field": name, "inferred_type": "native_text_or_json"}
                for name in fields
            ],
        },
        "field_mappings": [
            {
                "source_field": fields[0],
                "source_fields": list(fields if dependencies is None else dependencies),
                "canonical_field": "entity.legal_name",
                "confidence": 0.8,
                "semantic_evidence": docs,
                "transformation": "Execute the unchanged native-field expression.",
                "execution_pointer": "/execution/transforms/0",
                "validation_pass": False,
            }
        ],
        "execution": {"kind": "ast", "transforms": [expression]},
        "unmapped_fields": [],
    }, docs


def records(raw_rows):
    return [
        {
            "source_id": "invented-name-source",
            "source_record_id": str(index),
            "raw_record": copy.deepcopy(raw),
        }
        for index, raw in enumerate(raw_rows)
    ]


def branch(path, expected_fields, current_fields, docs, refs=(0,)):
    """Explicit independent declarations; never infer availability from the AST."""
    return {
        "branch_id": path,
        "documented_condition": docs,
        "expected_value_fields": list(expected_fields),
        "current_value_requirement": "available"
        if current_fields
        else "not_documented",
        "current_value_fields": list(current_fields),
        "coverage_mapping_indices": list(refs) if current_fields else [],
        "verdict": "supported",
        "evidence_quotes": [{"kind": "documentation", "quote": docs}],
        "reason": "Current-value availability is conditional on native presence; branch output dependencies are separate.",
    }


def review(branches, docs):
    return {
        "mapping_index": 0,
        "verdict": "supported",
        "evidence_quotes": [{"kind": "documentation", "quote": docs}],
        "reason": "The source defines the current name role and any business condition.",
        "repair_hint": "",
        "branch_reviews": branches,
    }


class ScriptedReviewer:
    def __init__(self, response, docs):
        self.response = {"reviews": [response]}
        self.docs = docs
        self.input = {}
        self.requests = []

    def model(self, purpose, payload, schema):
        self.requests.append(copy.deepcopy(payload))
        return copy.deepcopy(self.response)


def bound_audit(config, docs, response):
    return {
        "scope": "independent_name_role_model_review",
        "status": "supported",
        "name_mapping_count": 1,
        "human_gold": False,
        "candidate_config_hash": digest(config),
        "documentation_hash": digest(docs),
        "context_policy": "current_candidate_only",
        "errors": [],
        "reviews": [copy.deepcopy(response)],
    }


class ConditionalCoverageTests(unittest.TestCase):
    def assert_rejected(self, config, docs, response, rows):
        result = audit_name_mappings(ScriptedReviewer(response, docs), config, rows, [])
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["errors"])
        with self.assertRaises(Failure):
            validated_name_review(config, docs, bound_audit(config, docs, response))
        return result

    def test_same_field_empty_guard_keeps_available_obligation(self):
        for operations in (
            ("trim",),
            ("collapse_ws",),
            ("trim", "collapse_ws", "trim"),
        ):
            with self.subTest(operations=operations):
                expression = empty_guard(operations=operations)
                config, docs = configuration(expression)
                raw = [
                    {},
                    {"n24": None},
                    {"n24": " \t\n"},
                    {"n24": "  Current   Name  "},
                    {"n24": 0},
                ]
                rows = records(raw)
                expected_name = (
                    "Current Name" if "collapse_ws" in operations else "Current   Name"
                )
                expected_values = [None, None, None, expected_name, "0"]
                self.assertEqual(
                    [eval_ast(expression, row["raw_record"]) for row in rows],
                    expected_values,
                )
                response = review(
                    [
                        branch("/then", ["n24"], ["n24"], docs),
                        branch("/else", [], ["n24"], docs),
                    ],
                    docs,
                )
                response["branch_reviews"][1]["evidence_quotes"] = []
                runtime = ScriptedReviewer(response, docs)
                result = audit_name_mappings(runtime, config, rows, [])
                self.assertEqual(result["status"], "supported", result["errors"])
                self.assertEqual(len(runtime.requests), 1)
                self.assertEqual([row["raw_record"] for row in rows], raw)
                proof = result["branch_evidence"][0]["branches"][1][
                    "empty_normalization"
                ]
                self.assertEqual(proof["source_field"], "n24")
                self.assertEqual(
                    result["reviews"][0]["coverage_evidence"][1],
                    {
                        "branch_id": "/else",
                        "sampled_record_count": 3,
                        "accepted_probe_record_count": 0,
                        "basis": "host_proved_empty_normalization",
                        "semantic_truth_verified": False,
                    },
                )
                self.assertEqual(
                    [
                        eval_ast(proof["equivalent_expression"], row["raw_record"])
                        for row in rows
                    ],
                    expected_values,
                )

    def test_blank_equality_and_membership_preserve_exact_normalization(self):
        for condition in (
            {"op": "equals", "input": field("n24"), "value": None},
            {"op": "in", "input": field("n24"), "values": [None, "", " "]},
            {
                "op": "equals",
                "input": normalize("n24", ("collapse_ws",)),
                "value": "",
            },
        ):
            with self.subTest(condition=condition):
                expression = {
                    "op": "when",
                    "condition": condition,
                    "then": {"op": "omit", "reason": "Empty native value"},
                    "else": normalize("n24", ("collapse_ws", "trim")),
                }
                config, docs = configuration(expression)
                response = review(
                    [
                        branch("/then", [], ["n24"], docs),
                        branch("/else", ["n24"], ["n24"], docs),
                    ],
                    docs,
                )
                response["branch_reviews"][0]["evidence_quotes"] = []
                raw = [{"n24": value} for value in (None, "", " \t", " A  B ", 0)]
                result = audit_name_mappings(
                    ScriptedReviewer(response, docs), config, records(raw), []
                )
                self.assertEqual(result["status"], "supported", result["errors"])
                proof = result["branch_evidence"][0]["branches"][0][
                    "empty_normalization"
                ]
                self.assertEqual(proof["equivalent_expression"], expression["else"])
                self.assertEqual(
                    [eval_ast(expression, row) for row in raw],
                    [None, None, None, "A B", "0"],
                )
                self.assertEqual(
                    validated_name_review(config, docs, result), frozenset({0})
                )

    def test_available_empty_guard_validates_without_proposal_samples(self):
        for operations in (("trim",), ("collapse_ws",)):
            with self.subTest(operations=operations):
                config, docs = configuration(empty_guard(operations=operations))
                response = review(
                    [
                        branch("/then", ["n24"], ["n24"], docs),
                        branch("/else", [], ["n24"], docs),
                    ],
                    docs,
                )
                self.assertEqual(
                    validated_name_review(
                        config, docs, bound_audit(config, docs, response)
                    ),
                    frozenset({0}),
                )

    def test_nested_business_condition_and_empty_guard_have_distinct_obligations(self):
        docs = (
            "a17: Registered legal name captured on this row, including historical rows.\n"
            "b54: Y means a17 is current; null means a17 is historical.\n"
            "c99: Current registered legal name for historical rows when b54 is null."
        )
        expression = {
            "op": "when",
            "condition": {"op": "equals", "input": field("b54"), "value": "Y"},
            "then": normalize("a17", ("collapse_ws",)),
            "else": {
                "op": "when",
                "condition": {"op": "is_present", "input": field("b54")},
                "then": {"op": "omit", "reason": "Outside documented Y/null states"},
                "else": empty_guard("c99", ("collapse_ws",)),
            },
        }
        config, docs = configuration(expression, ("a17", "b54", "c99"), docs)
        response = review(
            [
                branch("/then", ["a17"], ["a17"], docs),
                branch("/else", ["c99"], ["c99"], docs),
                branch("/else/then", [], [], docs),
                branch("/else/else", ["c99"], ["c99"], docs),
                branch("/else/else/then", ["c99"], ["c99"], docs),
                branch("/else/else/else", [], ["c99"], docs),
            ],
            docs,
        )
        rows = records(
            [
                {"a17": "Current One", "b54": "Y", "c99": None},
                {"a17": "Former Two", "b54": None, "c99": "  Current Two  "},
                {"a17": "Former Three", "b54": None, "c99": " "},
            ]
        )
        self.assertEqual(
            [eval_ast(expression, row["raw_record"]) for row in rows],
            ["Current One", "Current Two", None],
        )
        for exposed in (rows, rows[:1], []):
            with self.subTest(exposed_records=len(exposed)):
                result = audit_name_mappings(
                    ScriptedReviewer(response, docs), config, exposed, []
                )
                self.assertEqual(result["status"], "supported", result["errors"])
                self.assertEqual(
                    validated_name_review(config, docs, result), frozenset({0})
                )

    def test_present_current_value_cannot_be_omitted(self):
        expression = empty_guard()
        expression["then"] = {"op": "omit", "reason": "Drop nonempty current name"}
        config, docs = configuration(expression)
        response = review(
            [branch("/then", [], ["n24"], docs), branch("/else", [], ["n24"], docs)],
            docs,
        )
        self.assertIsNone(eval_ast(expression, {"n24": "Available Current Name"}))
        self.assert_rejected(
            config, docs, response, records([{"n24": "Available Current Name"}])
        )

    def test_empty_guard_cannot_discard_another_current_companion(self):
        docs = "n24: Current legal name when filled; when blank, c99 holds the current legal name."
        config, docs = configuration(
            empty_guard(), ("n24", "c99"), docs, dependencies=("n24",)
        )
        response = review(
            [
                branch("/then", ["n24"], ["n24"], docs),
                branch("/else", [], ["c99"], docs),
            ],
            docs,
        )
        rows = records([{"n24": "", "c99": "Available Current Name"}])
        self.assert_rejected(config, docs, response, rows)

    def test_inner_empty_proof_cannot_erase_outer_business_condition(self):
        docs = "n24: Current registered legal name. m08: H marks historical rows; c99 holds their current registered legal name."
        expression = {
            "op": "when",
            "condition": {"op": "equals", "input": field("m08"), "value": "H"},
            "then": {
                "op": "omit",
                "reason": "Drop historical row including current companion",
            },
            "else": empty_guard(),
        }
        config, docs = configuration(
            expression, ("n24", "m08", "c99"), docs, dependencies=("n24", "m08")
        )
        response = review(
            [
                branch("/then", [], ["c99"], docs),
                branch("/else", ["n24"], ["n24"], docs),
                branch("/else/then", ["n24"], ["n24"], docs),
                branch("/else/else", [], ["n24"], docs),
            ],
            docs,
        )
        result = self.assert_rejected(
            config,
            docs,
            response,
            records([{"m08": "H", "n24": "Former", "c99": "Current"}]),
        )
        self.assertTrue(
            any(
                d["branch_id"] == "/then"
                for error in result["errors"]
                for d in error.get("branch_diagnostics", [])
            )
        )

    def test_empty_predicate_cannot_prove_another_field_or_arbitrary_transform(self):
        for value in (
            normalize("c99"),
            {
                "op": "enum_lookup",
                "input": field("n24"),
                "values": {"Keep": "Changed"},
                "default": None,
            },
        ):
            with self.subTest(value=value):
                expression = empty_guard()
                expression["then"] = value
                produced = ["c99"] if value.get("op") == "trim" else ["n24"]
                config, docs = configuration(
                    expression,
                    ("n24", "c99"),
                    dependencies=("n24", "c99") if produced == ["c99"] else ("n24",),
                )
                response = review(
                    [
                        branch("/then", produced, ["n24"], docs),
                        branch("/else", [], ["n24"], docs),
                    ],
                    docs,
                )
                result = self.assert_rejected(config, docs, response, [])
                self.assertTrue(
                    all(
                        "empty_normalization" not in item
                        for item in result["branch_evidence"][0]["branches"]
                    )
                )

    def test_uncertain_current_value_requirement_remains_blocking(self):
        config, docs = configuration(empty_guard())
        response = review(
            [
                branch("/then", ["n24"], ["n24"], docs),
                branch("/else", [], ["n24"], docs),
            ],
            docs,
        )
        response["branch_reviews"][1]["current_value_requirement"] = "uncertain"
        self.assert_rejected(config, docs, response, [])

    def test_unknown_operation_cannot_create_an_empty_proof(self):
        expression = empty_guard()
        expression["then"]["op"] = "unknown_name_normalizer"
        config, docs = configuration(expression)
        response = review(
            [
                branch("/then", ["n24"], ["n24"], docs),
                branch("/else", [], ["n24"], docs),
            ],
            docs,
        )
        runtime = ScriptedReviewer(response, docs)
        result = audit_name_mappings(runtime, config, [], [])
        self.assertEqual(result["status"], "local_validation_failed")
        self.assertEqual(runtime.requests, [])
        with self.assertRaises(Failure):
            validated_name_review(config, docs, bound_audit(config, docs, response))

    def test_forged_saved_proof_and_flags_do_not_authorize_omission(self):
        expression = empty_guard()
        expression["then"] = {"op": "omit", "reason": "Drop available current name"}
        config, docs = configuration(expression)
        response = review(
            [branch("/then", [], ["n24"], docs), branch("/else", [], ["n24"], docs)],
            docs,
        )
        saved = bound_audit(config, docs, response)
        saved["reviews"][0].update(
            evidence_verified=True, branch_analysis_verified=True, coverage_evidence=[]
        )
        saved["branch_evidence"] = {
            "0": {
                "branches": [
                    {
                        "branch_id": "/then",
                        "possible_value_fields": ["n24"],
                        "empty_normalization": {
                            "kind": "host_proved_empty_normalization",
                            "source_field": "n24",
                            "equivalent_expression": normalize("n24"),
                            "omit_arm": "then",
                        },
                    }
                ]
            }
        }
        with self.assertRaises(Failure):
            validated_name_review(config, docs, saved)


if __name__ == "__main__":
    unittest.main()
