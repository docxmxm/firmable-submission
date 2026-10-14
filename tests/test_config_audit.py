"""Host review contracts with renamed fields; scripted reviews are not model evals."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.common import Failure
from firmable_runtime.config_audit import audit_name_mappings, review_schema
from firmable_runtime.engine import compile_mappings, eval_ast
from firmable_runtime.execution import compile_config, run_engine
from firmable_runtime.openrouter_wire import OpenRouterWireContract
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response


DOCS = """a17: Registered legal name captured on this row, including historical rows.
b54: Flag indicating whether a17 is current. Y means current, null means historical.
c99: Current legal name for historical rows when b54 is null.
"""
RULE = "b54: Flag indicating whether a17 is current. Y means current, null means historical."


def fixture(conditional=False):
    ast = {"op": "field", "name": "a17"}
    if conditional:
        ast = {
            "op": "when",
            "condition": {
                "op": "equals",
                "input": {"op": "field", "name": "b54"},
                "value": "Y",
            },
            "then": ast,
            "else": {"op": "field", "name": "c99"},
        }
    config = {
        "field_mappings": [
            {
                "source_field": "a17",
                "source_fields": ["a17", "b54", "c99"] if conditional else ["a17"],
                "canonical_field": "entity.legal_name",
                "confidence": 0.99,
                "transformation": "MAPPER_SELF_ASSERTION_ALWAYS_CORRECT",
                "semantic_evidence": "MAPPER_SELF_ASSERTION_ALWAYS_CORRECT",
                "execution_pointer": "/execution/transforms/0",
                "validation_pass": False,
            }
        ],
        "source_schema": {
            "fields": [{"source_field": name} for name in ("a17", "b54", "c99")]
        },
        "execution": {"kind": "ast", "transforms": [ast]},
        "unmapped_fields": [],
    }
    records = [
        {
            "source_id": "renamed-source",
            "source_record_id": "r1",
            "raw_record": {"a17": "Current One Ltd", "b54": "Y", "c99": None},
        },
        {
            "source_id": "renamed-source",
            "source_record_id": "r2",
            "raw_record": {
                "a17": "Former Two Ltd",
                "b54": None,
                "c99": "Current Two Ltd",
            },
        },
    ]
    observations = [
        {
            "source_id": row["source_id"],
            "source_record_id": row["source_record_id"],
            "entity": {"legal_name": eval_ast(ast, row["raw_record"])},
        }
        for row in records
    ]
    return config, records, observations


def review(verdict="supported", quotes=None, index=0):
    return {
        "mapping_index": index,
        "verdict": verdict,
        "evidence_quotes": quotes
        if quotes is not None
        else [{"kind": "documentation", "quote": RULE}],
        "reason": "The original flag rule must determine which name is current.",
        "repair_hint": "Use the documented marker to choose the current name; retain exact dependencies.",
    }


def complementary_fixture():
    config, records, observations = fixture(conditional=True)
    first = config["field_mappings"][0]
    first["source_fields"] = ["a17", "b54"]
    config["field_mappings"].append(
        {
            **first,
            "source_field": "c99",
            "source_fields": ["c99", "b54"],
            "execution_pointer": "/execution/transforms/1",
        }
    )
    ast = config["execution"]["transforms"][0]
    ast["else"] = {"op": "omit", "reason": "Historical local value"}
    config["execution"]["transforms"].append(
        {
            "op": "when",
            "condition": copy.deepcopy(ast["condition"]),
            "then": {"op": "omit", "reason": "Other mapping covers the value"},
            "else": {"op": "field", "name": "c99"},
        }
    )
    reviews = []
    for index in (0, 1):
        row = review(index=index)
        row["branch_reviews"] = [
            {
                "branch_id": arm,
                "documented_condition": RULE,
                "expected_value_fields": fields if index == ref else [],
                "current_value_requirement": "available",
                "current_value_fields": fields,
                "coverage_mapping_indices": [ref],
                "verdict": "supported",
                "evidence_quotes": row["evidence_quotes"],
                "reason": "The current mapping references supply the documented value.",
            }
            for arm, fields, ref in (("/then", ["a17"], 0), ("/else", ["c99"], 1))
        ]
        reviews.append(row)
    return config, records, observations, reviews


def ordinary_name_fixture(header, target="entity.trading_name"):
    config, _, _ = fixture()
    config["source_schema"] = {
        "format": "json",
        "data_files": ["invented.json"],
        "discovery_method": "invented native fixture",
        "fields": [{"source_field": header, "inferred_type": "string"}],
    }
    config["field_mappings"][0].update(
        source_field=header,
        source_fields=[header],
        canonical_field=target,
        semantic_evidence=header,
    )
    config["execution"]["transforms"] = [
        {"op": "trim", "input": {"op": "field", "name": header}}
    ]
    records = [
        {
            "source_id": "invented-source",
            "source_record_id": str(index),
            "raw_record": {header: value},
        }
        for index, value in enumerate(("  Example Ltd  ", " ", None))
    ]
    return config, records


def name_relation_fixture(labels=("v11", "v22", "v33"), *, merged=False):
    """Opaque headers whose roles require original field definitions to combine."""
    config, records, observations, _ = complementary_fixture()
    if merged:
        config, records, observations = fixture(conditional=True)
    replacements = dict(zip(("a17", "c99", "b54"), labels))

    def rename(value):
        if isinstance(value, dict):
            return {replacements.get(k, k): rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    config, records, observations = rename([config, records, observations])
    config["source_schema"].update(
        format="json",
        data_files=["invented.json"],
        discovery_method="synthetic fixture",
    )
    for field in config["source_schema"]["fields"]:
        field["inferred_type"] = "string"
    for mapping in config["field_mappings"]:
        mapping["semantic_evidence"] = mapping["source_field"]
    base, current, flag = labels
    docs = (
        "| Field | Description | Type | Parameters |\n| --- | --- | --- | --- |\n"
        f"| {base} | Company name as it appears on the register | Text | |\n"
        f"| {current} | Current company name | Text | |\n"
        f"| {flag} | Whether the displayed name is current | Text | Y or null |\n"
    )
    return config, records, observations, docs


class ReviewRuntime:
    def __init__(self, reviews, docs=DOCS):
        self.docs, self.input, self.requests = docs, {}, []
        self.response = {"reviews": reviews}

    def model(self, purpose, payload, schema):
        self.requests.append((purpose, copy.deepcopy(payload), copy.deepcopy(schema)))
        response = scripted_branches(self.response, payload)
        check_response(response, schema)
        return response


def scripted_branches(response, payload):
    """Scripted branch declarations, never a real-model semantic evaluation."""
    response = copy.deepcopy(response)
    mappings = {m["mapping_index"]: m for m in payload["name_mappings"]}
    for review in response["reviews"]:
        item = mappings.get(review["mapping_index"], next(iter(mappings.values())))
        review.setdefault(
            "branch_reviews",
            [
                {
                    "branch_id": branch["branch_id"],
                    "documented_condition": "Original fixture condition for this branch.",
                    "expected_value_fields": branch["possible_value_fields"]
                    if branch["possible_value_fields"] is not None
                    else item["source_fields"],
                    "verdict": review["verdict"],
                    "evidence_quotes": copy.deepcopy(review["evidence_quotes"]),
                    "reason": "Scripted comparison with original fixture field role.",
                }
                for branch in item["host_branch_evidence"]["branches"]
            ],
        )
        for branch in review["branch_reviews"]:
            fields = branch["expected_value_fields"]
            branch.setdefault(
                "current_value_requirement", "available" if fields else "not_documented"
            )
            branch.setdefault("current_value_fields", copy.deepcopy(fields))
            branch.setdefault(
                "coverage_mapping_indices", [review["mapping_index"]] if fields else []
            )
    return response


class ReviewProvider:
    """Known replies and unknown outcomes, through the real durable call journal."""

    model_version = "offline-name-review"

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response = scripted_branches(response, request["payload"])
        check_response(response, request["output_schema"])
        return {"config": response, "cost_usd": 0, "usage": {}}


def durable_request(root):
    root = Path(root)
    evidence = root / "evidence"
    evidence.mkdir()
    _, records, _ = fixture(conditional=True)
    (evidence / "rows.json").write_text(
        json.dumps([record["raw_record"] for record in records])
    )
    (evidence / "definitions.md").write_text(DOCS)
    ontology = root / "ontology.yaml"
    ontology.write_text("entity:\n  legal_name: string\n")
    return {
        "source_id": "renamed-source",
        "evidence_root": str(evidence),
        "state_dir": str(root / "run"),
        "ontology_path": str(ontology),
    }


class NameReviewTests(unittest.TestCase):
    def name_relation_payload(self, config, records, docs):
        runtime = ReviewRuntime(
            [
                review(
                    "uncertain", [{"kind": "header", "quote": m["source_field"]}], index
                )
                for index, m in enumerate(config["field_mappings"])
                if m["canonical_field"] in {"entity.legal_name", "entity.trading_name"}
            ],
            docs,
        )
        result = audit_name_mappings(runtime, config, records, [])
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["human_gold"])
        return runtime.requests[0][1]

    @staticmethod
    def name_relation_binding(payload, field):
        return next(
            binding
            for item in payload["name_mappings"]
            for binding in item["host_semantic_evidence"]["fields"]
            if binding["source_field"] == field
        )

    def test_compiler_name_scope_reaches_split_merged_renamed_and_reordered_hints(self):
        for labels in (("v11", "v22", "v33"), ("Entry Alpha", "Entry Beta", "Switch")):
            for merged in (False, True):
                for reverse in (False, True):
                    with self.subTest(labels=labels, merged=merged, reverse=reverse):
                        config, records, _, docs = name_relation_fixture(
                            labels, merged=merged
                        )
                        if reverse:
                            config["field_mappings"].reverse()
                        scope = {}
                        compiled = compile_config(config, docs, name_group_scope=scope)
                        self.assertEqual(len(compiled), 1 if merged else 2)
                        self.assertEqual(
                            scope, {"entity.legal_name": sorted(labels[:2])}
                        )
                        payload = self.name_relation_payload(config, records, docs)
                        binding = self.name_relation_binding(payload, labels[1])
                        self.assertTrue(binding["role_check"]["supported"])
                        relation = binding["role_check"]["register_name_relations"][0]
                        self.assertEqual(relation["register_name_field"], labels[0])
                        self.assertEqual(relation["current_name_field"], labels[1])
                        for definition in relation["field_definitions"].values():
                            self.assertIn(definition["text"], docs)
                            self.assertIn(
                                {"kind": "documentation", "quote": definition["text"]},
                                binding["evidence_quotes"],
                            )
                        for item in payload["name_mappings"]:
                            self.assertEqual(
                                item["host_semantic_evidence"][
                                    "same_target_value_fields"
                                ],
                                scope["entity.legal_name"],
                            )

    def test_complete_local_hint_relation_precedes_extra_same_target_anchor(self):
        config, records, _, docs = name_relation_fixture(merged=True)
        config["source_schema"]["fields"].append(
            {"source_field": "Other label", "inferred_type": "string"}
        )
        docs += (
            "| Other label | Company name as it appears on the register | Text | |\n"
        )
        peer = copy.deepcopy(config["field_mappings"][0])
        peer.update(
            source_field="Other label",
            source_fields=["Other label"],
            semantic_evidence="Other label",
            execution_pointer="/execution/transforms/1",
        )
        config["field_mappings"].append(peer)
        config["execution"]["transforms"].append({"op": "field", "name": "Other label"})
        for reverse in (False, True):
            if reverse:
                config["field_mappings"].reverse()
            compile_config(config, docs)
            binding = self.name_relation_binding(
                self.name_relation_payload(config, records, docs), "v22"
            )
            self.assertTrue(binding["role_check"]["supported"])
            relation = binding["role_check"]["register_name_relations"][0]
            self.assertEqual(relation["register_name_field"], "v11")
            self.assertNotIn("Other label", relation["field_definitions"])

    def test_unrelated_compile_failure_preserves_checked_name_scope_without_approval(
        self,
    ):
        config, records, _, docs = name_relation_fixture()
        config["source_schema"]["fields"].append(
            {"source_field": "Numeric code", "inferred_type": "string"}
        )
        peer = copy.deepcopy(config["field_mappings"][0])
        peer.update(
            source_field="Numeric code",
            source_fields=["Numeric code"],
            semantic_evidence="Numeric code",
            canonical_field="entity.abn",
            execution_pointer="/execution/transforms/2",
        )
        config["field_mappings"].append(peer)
        config["execution"]["transforms"].append(
            {"op": "field", "name": "Numeric code"}
        )
        scope = {}
        with self.assertRaises(Failure):
            compile_config(config, docs, name_group_scope=scope)
        self.assertEqual(scope, {"entity.legal_name": ["v11", "v22"]})
        payload = self.name_relation_payload(config, records, docs)
        self.assertTrue(
            self.name_relation_binding(payload, "v22")["role_check"]["supported"]
        )
        self.assertIn(
            "whole-config success",
            payload["name_mappings"][0]["host_semantic_evidence"]["limitation"],
        )

    def test_unqualified_peers_cannot_lend_role_to_review_hints(self):
        for fault in (
            "cross_target",
            "foreign_subject",
            "bad_quote",
            "bad_confidence",
            "predicate_only",
        ):
            with self.subTest(fault=fault):
                labels = (
                    ("Director label", "v22", "v33")
                    if fault == "foreign_subject"
                    else ("v11", "v22", "v33")
                )
                config, records, _, docs = name_relation_fixture(labels)
                peer = config["field_mappings"][0]
                if fault == "cross_target":
                    peer["canonical_field"] = "entity.trading_name"
                elif fault == "bad_quote":
                    peer["semantic_evidence"] = "MAPPER_ASSERTION_NOT_SOURCE"
                elif fault == "bad_confidence":
                    peer["confidence"] = 2
                elif fault == "predicate_only":
                    peer["source_fields"] = [labels[0]]
                    config["execution"]["transforms"][0] = {
                        "op": "when",
                        "condition": {
                            "op": "is_present",
                            "input": {"op": "field", "name": labels[0]},
                        },
                        "then": {"op": "omit", "reason": "No value claim"},
                        "else": {"op": "omit", "reason": "No value claim"},
                    }
                payload = self.name_relation_payload(config, records, docs)
                binding = self.name_relation_binding(payload, "v22")
                self.assertFalse(binding["role_check"]["supported"])
                item = next(
                    m for m in payload["name_mappings"] if m["source_field"] == "v22"
                )
                self.assertNotIn(
                    labels[0],
                    item["host_semantic_evidence"]["same_target_value_fields"],
                )
                self.assertNotIn("MAPPER_ASSERTION_NOT_SOURCE", json.dumps(payload))

    def test_group_scope_does_not_override_generic_contradictory_or_foreign_value(self):
        for fault in ("generic", "contradiction", "foreign_subject"):
            with self.subTest(fault=fault):
                labels = (
                    ("v11", "Director label", "v33")
                    if fault == "foreign_subject"
                    else ("v11", "v22", "v33")
                )
                config, records, _, docs = name_relation_fixture(labels)
                if fault == "generic":
                    docs = docs.replace(
                        "Current company name", "Free-form display text"
                    )
                elif fault == "contradiction":
                    docs += "| v22 | Not the registered company name | Text | |\n"
                payload = self.name_relation_payload(config, records, docs)
                binding = self.name_relation_binding(payload, labels[1])
                self.assertFalse(
                    binding[
                        "subject_check" if fault == "foreign_subject" else "role_check"
                    ]["supported"]
                )
                with self.assertRaises(Failure):
                    compile_config(config, docs)

    def test_invalid_ast_or_dependencies_cannot_contribute_scope_or_be_reviewed(self):
        for fault in ("ast", "dependencies"):
            with self.subTest(fault=fault):
                config, records, _, docs = name_relation_fixture()
                if fault == "ast":
                    config["execution"]["transforms"][0]["op"] = "invented_op"
                else:
                    config["field_mappings"][0]["source_fields"] = ["v33"]
                scope = {}
                with self.assertRaises(Failure):
                    compile_config(config, docs, name_group_scope=scope)
                self.assertNotIn("v11", scope.get("entity.legal_name", []))
                runtime = ReviewRuntime([], docs)
                result = audit_name_mappings(runtime, config, records, [])
                self.assertEqual(result["status"], "local_validation_failed")
                self.assertEqual(runtime.requests, [])

    def test_name_scope_collector_is_cleared_before_schema_failure(self):
        config, _, _, docs = name_relation_fixture()
        scope = {}
        compile_config(config, docs, name_group_scope=scope)
        self.assertTrue(scope)
        with self.assertRaises(Failure):
            compile_config({}, docs, name_group_scope=scope)
        self.assertEqual(scope, {})

    def test_current_companion_references_are_checked_without_old_opinions(self):
        config, records, observations, reviews = complementary_fixture()
        previous = audit_name_mappings(
            ReviewRuntime(reviews), config, records, observations
        )
        for broken in (
            "deleted",
            "other_target",
            "abstaining",
            "wrong_value",
            "wrong_condition",
            "unresolved",
        ):
            with self.subTest(broken=broken):
                candidate, claims = copy.deepcopy(config), copy.deepcopy(reviews)
                if broken == "deleted":
                    candidate["field_mappings"].pop()
                    candidate["execution"]["transforms"].pop()
                    claims.pop()
                elif broken == "other_target":
                    candidate["field_mappings"][1]["canonical_field"] = (
                        "entity.trading_name"
                    )
                elif broken == "unresolved":
                    claims[1]["verdict"] = "uncertain"
                    for branch in claims[1]["branch_reviews"]:
                        branch["verdict"] = "uncertain"
                elif broken == "wrong_condition":
                    expression = candidate["execution"]["transforms"][1]
                    expression["then"], expression["else"] = (
                        expression["else"],
                        expression["then"],
                    )
                    claims[1]["branch_reviews"][0]["expected_value_fields"] = ["c99"]
                    claims[1]["branch_reviews"][1]["expected_value_fields"] = []
                else:
                    expression = {"op": "omit", "reason": "No value"}
                    dependencies = ["b54"]
                    if broken == "wrong_value":
                        expression = {"op": "field", "name": "a17"}
                        dependencies.append("a17")
                    candidate["execution"]["transforms"][1]["else"] = expression
                    candidate["field_mappings"][1]["source_fields"] = dependencies
                    claims[1]["branch_reviews"][1]["expected_value_fields"] = (
                        ["a17"] if broken == "wrong_value" else []
                    )
                claims[0]["reason"] = "The other mapping supplies the current value."
                runtime = ReviewRuntime(claims)
                result = audit_name_mappings(runtime, candidate, records, [], previous)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(
                    result["errors"][0]["kind"], "name_role_branch_invalid"
                )
                self.assertFalse(result["human_gold"])
                self.assertEqual(len(runtime.requests), 4)
                self.assertTrue(
                    all(
                        "prior_candidate_review" not in p
                        for _, p, _ in runtime.requests
                    )
                )
                self.assertTrue(result["errors"][0]["branch_diagnostics"])

    def test_complementary_trace_separates_accepted_output_from_isolated_values(self):
        config, records, observations, reviews = complementary_fixture()
        for actual in (observations, []):
            with self.subTest(accepted=bool(actual)):
                runtime = ReviewRuntime(reviews)
                result = audit_name_mappings(runtime, config, records, actual)
                self.assertEqual(result["status"], "supported")
                group = result["group_evidence"]["groups"][0]
                self.assertEqual(
                    [
                        [m["isolated_expression_result"] for m in r["mapping_results"]]
                        for r in group["records"]
                    ],
                    [["Current One Ltd", None], [None, "Current Two Ltd"]],
                )
                self.assertEqual(
                    bool(group["records"][0]["actual_probe_outputs"]), bool(actual)
                )
                self.assertLessEqual(
                    result["group_evidence"]["trace_json_utf8_bytes"], 8192
                )
        missing = copy.deepcopy(observations)
        missing[1]["entity"] = {}
        result = audit_name_mappings(ReviewRuntime(reviews), config, records, missing)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("Actual probe output", json.dumps(result["errors"]))
        records[1]["raw_record"]["c99"] = None
        result = audit_name_mappings(ReviewRuntime(reviews), config, records, missing)
        self.assertEqual(result["status"], "supported")

    def test_reordered_candidate_cannot_reference_an_old_name_mapping_index(self):
        config, records, observations, reviews = complementary_fixture()
        previous = audit_name_mappings(
            ReviewRuntime(reviews), config, records, observations
        )
        current_name = copy.deepcopy(config["field_mappings"][0])
        current_name["execution_pointer"] = "/execution/transforms/2"
        config["field_mappings"][0]["canonical_field"] = "entity.status"
        config["field_mappings"][1]["canonical_field"] = "entity.abn"
        config["field_mappings"].append(current_name)
        config["execution"]["transforms"].append(
            copy.deepcopy(config["execution"]["transforms"][0])
        )
        claim = copy.deepcopy(reviews[0])
        claim["mapping_index"] = 2
        claim["branch_reviews"][0]["coverage_mapping_indices"] = [2]
        runtime = ReviewRuntime([claim])
        result = audit_name_mappings(runtime, config, records, [], previous)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("not a current mapping", json.dumps(result["errors"]))
        self.assertEqual(
            runtime.requests[0][1]["name_review_contract"][
                "same_target_mapping_groups"
            ],
            [{"canonical_field": "entity.legal_name", "mapping_indices": [2]}],
        )

    def test_coverage_requires_structured_current_fields_and_references(self):
        config, records, observations, reviews = complementary_fixture()
        for change in (
            {"coverage_mapping_indices": []},
            {"coverage_mapping_indices": [0]},
            {"coverage_mapping_indices": [0], "current_value_fields": ["a17"]},
            {"current_value_requirement": "uncertain"},
            {"current_value_requirement": "not_documented"},
            {"current_value_fields": []},
        ):
            with self.subTest(change=change):
                claims = copy.deepcopy(reviews)
                claims[0]["branch_reviews"][1].update(change)
                result = audit_name_mappings(
                    ReviewRuntime(claims), config, records, observations
                )
                self.assertEqual(result["status"], "rejected")
                self.assertFalse(result["reviews"][0]["branch_analysis_verified"])

    def test_unsampled_complement_has_same_result_as_merged_and_static_basis(self):
        merged, records, observations = fixture(conditional=True)
        merged_result = audit_name_mappings(
            ReviewRuntime([review()]), merged, records[:1], observations[:1]
        )
        config, records, observations, reviews = complementary_fixture()
        result = audit_name_mappings(
            ReviewRuntime(reviews), config, records[:1], observations[:1]
        )
        self.assertEqual(merged_result["status"], "supported")
        self.assertEqual(result["status"], merged_result["status"])
        basis = result["reviews"][0]["coverage_evidence"][1]
        self.assertEqual(basis["sampled_record_count"], 0)
        self.assertEqual(basis["accepted_probe_record_count"], 0)
        self.assertEqual(basis["basis"], "same_condition_ast")
        config["execution"]["transforms"][1]["condition"]["value"] = "N"
        result = audit_name_mappings(
            ReviewRuntime(reviews), config, records[:1], observations[:1]
        )
        self.assertEqual(result["status"], "rejected")
        self.assertIn("not proved", json.dumps(result["errors"]))

    def test_task_cannot_claim_coverage_from_undeclared_fields(self):
        config, records, observations = fixture()
        config["execution"] = {"kind": "llm_task", "task": "Use only a17 as the name."}
        config["field_mappings"][0]["execution_pointer"] = "/execution/task"
        row = review()
        row["branch_reviews"] = [
            {
                "branch_id": "/",
                "documented_condition": RULE,
                "expected_value_fields": ["a17"],
                "current_value_requirement": "available",
                "current_value_fields": ["c99"],
                "coverage_mapping_indices": [0],
                "verdict": "supported",
                "evidence_quotes": row["evidence_quotes"],
                "reason": "Wrongly claim the unavailable companion supplies the value.",
            }
        ]
        result = audit_name_mappings(
            ReviewRuntime([row]), config, records, observations
        )
        self.assertEqual(result["status"], "rejected")
        self.assertIn("not declared mapping dependencies", json.dumps(result["errors"]))

    def test_review_wire_schema_stays_flat_and_checks_coverage_contract(self):
        schema = review_schema([0, 1])
        contract = OpenRouterWireContract(schema)
        self.assertEqual(
            contract.complexity, {"optional_parameters": 0, "union_parameters": 0}
        )
        self.assertLess(len(json.dumps(contract.schema)), 6000)
        _, _, _, reviews = complementary_fixture()
        response = {"reviews": reviews}
        self.assertEqual(contract.decode(contract.encode(response)), response)
        del response["reviews"][0]["branch_reviews"][0]["coverage_mapping_indices"]
        with self.assertRaises(Failure):
            check_response(response, schema)

    def test_role_bearing_native_headers_supply_real_execution_and_review_evidence(
        self,
    ):
        for header, target in (
            ("OperatingName", "entity.trading_name"),
            ("trading-name", "entity.trading_name"),
            ("DBA", "entity.trading_name"),
            ("RegisteredLegalName", "entity.legal_name"),
        ):
            for empty_dictionary in (False, True):
                with self.subTest(header=header, empty_dictionary=empty_dictionary):
                    config, records = ordinary_name_fixture(header, target)
                    docs = (
                        "| Field | Description |\n| --- | --- |\n| " + header + " | |"
                        if empty_dictionary
                        else ""
                    )
                    observations, report = run_engine(
                        config,
                        records,
                        docs,
                        {"licence": "CC BY 4.0"},
                        "2026-01-01T00:00:00+00:00",
                    )
                    self.assertEqual(report["errors"], [])
                    key = target.split(".")[1]
                    self.assertEqual(
                        [row.get("entity", {}).get(key) for row in observations],
                        ["Example Ltd", None, None],
                    )
                    self.assertEqual(
                        [row["raw_record"] for row in observations],
                        [row["raw_record"] for row in records],
                    )
                    runtime = ReviewRuntime(
                        [review(quotes=[{"kind": "header", "quote": header}])], docs
                    )
                    result = audit_name_mappings(runtime, config, records, observations)
                    self.assertEqual(result["status"], "supported")
                    payload = runtime.requests[0][1]
                    binding = payload["name_mappings"][0]["host_semantic_evidence"][
                        "fields"
                    ][0]
                    self.assertTrue(binding["role_check"]["supported"])
                    self.assertTrue(binding["subject_check"]["supported"])
                    self.assertIn(
                        {"kind": "header", "quote": header}, binding["evidence_quotes"]
                    )
                    self.assertFalse(
                        payload["host_evidence_policy"][
                            "separate_description_or_form_crosswalk_always_required"
                        ]
                    )

    def test_generic_header_and_mapper_assertion_do_not_establish_legal_role(self):
        for header in ("EntityName", "DisplayLabel", "Name"):
            with self.subTest(header=header):
                config, records = ordinary_name_fixture(header, "entity.legal_name")
                with self.assertRaises(Failure) as error:
                    run_engine(config, records, "", {}, "2026-01-01T00:00:00+00:00")
                self.assertIn("No unambiguous same-field", str(error.exception))
                config["field_mappings"][0]["semantic_evidence"] = (
                    "MAPPER_SELF_ASSERTION: the registered legal name."
                )
                runtime = ReviewRuntime(
                    [review("uncertain", [{"kind": "header", "quote": header}])], ""
                )
                result = audit_name_mappings(runtime, config, records, [])
                self.assertEqual(result["errors"][0]["kind"], "name_role_uncertain")
                payload = runtime.requests[0][1]
                binding = payload["name_mappings"][0]["host_semantic_evidence"][
                    "fields"
                ][0]
                self.assertFalse(binding["role_check"]["supported"])
                self.assertNotIn("MAPPER_SELF_ASSERTION", json.dumps(payload))

    def test_header_role_does_not_override_original_contradiction_or_foreign_subject(
        self,
    ):
        header = "OperatingName"
        for definition, role_supported, subject_supported in (
            ("Not a trading name; internal display text only.", False, True),
            ("Trading name of the venue.", True, False),
        ):
            with self.subTest(definition=definition):
                config, records = ordinary_name_fixture(header)
                docs = header + ": " + definition
                with self.assertRaises(Failure) as error:
                    run_engine(config, records, docs, {}, "2026-01-01T00:00:00+00:00")
                self.assertIn("Static compilation failed", str(error.exception))
                runtime = ReviewRuntime(
                    [
                        review(
                            "contradicted", [{"kind": "documentation", "quote": docs}]
                        )
                    ],
                    docs,
                )
                result = audit_name_mappings(runtime, config, records, [])
                self.assertEqual(result["errors"][0]["kind"], "name_role_contradicted")
                binding = runtime.requests[0][1]["name_mappings"][0][
                    "host_semantic_evidence"
                ]["fields"][0]
                self.assertEqual(binding["role_check"]["supported"], role_supported)
                self.assertEqual(
                    binding["subject_check"]["supported"], subject_supported
                )
                self.assertIn(
                    {"kind": "documentation", "quote": docs}, binding["evidence_quotes"]
                )

    def test_finite_header_support_never_automatically_accepts_uncertain_review(self):
        config, records = ordinary_name_fixture("OperatingName")
        runtime = ReviewRuntime(
            [review("uncertain", [{"kind": "header", "quote": "OperatingName"}])], ""
        )
        result = audit_name_mappings(runtime, config, records, [])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"][0]["kind"], "name_role_uncertain")
        self.assertEqual(len(runtime.requests), 2)
        for _, payload, _ in runtime.requests:
            self.assertTrue(
                payload["name_mappings"][0]["host_semantic_evidence"]["fields"][0][
                    "role_check"
                ]["supported"]
            )

    def test_empty_normalization_proof_matches_real_execution_without_blank_docs(self):
        field = {"op": "field", "name": "n24"}
        trimmed = {"op": "trim", "input": field}
        docs = "n24: Trading name of the business."
        conditions = [
            ({"op": "equals", "input": trimmed, "value": ""}, "else"),
            ({"op": "equals", "input": field, "value": None}, "else"),
            ({"op": "in", "input": field, "values": [None, "", " "]}, "else"),
            ({"op": "is_present", "input": field}, "then"),
        ]
        # Fixed outcomes, not expectations derived from the implementation under test.
        raw_values = [None, "", " \t\n", "  Example Ltd  ", 0, "0", "Retired Ltd"]
        expected_values = [None, None, None, "Example Ltd", "0", "0", "Retired Ltd"]
        for condition, value_arm in conditions:
            with self.subTest(condition=condition):
                config, _, _ = fixture()
                config["source_schema"] = {
                    "format": "json",
                    "data_files": ["invented.json"],
                    "discovery_method": "invented native fixture",
                    "fields": [{"source_field": "n24", "inferred_type": "string"}],
                }
                config["field_mappings"][0].update(
                    source_field="n24",
                    source_fields=["n24"],
                    canonical_field="entity.trading_name",
                    semantic_evidence=docs,
                )
                omit_arm = "else" if value_arm == "then" else "then"
                expression = {
                    "op": "when",
                    "condition": condition,
                    value_arm: trimmed,
                    omit_arm: {"op": "omit", "reason": "MAPPER_ASSERTION_TO_HIDE"},
                }
                config["execution"]["transforms"] = [expression]
                records = [
                    {
                        "source_id": "invented-source",
                        "source_record_id": str(index),
                        "raw_record": {"n24": value},
                    }
                    for index, value in enumerate(raw_values)
                ]
                observations, report = run_engine(
                    config,
                    records,
                    docs,
                    {"licence": "CC BY 4.0"},
                    "2026-01-01T00:00:00+00:00",
                )
                self.assertEqual(report["errors"], [])
                self.assertEqual(
                    [row.get("entity", {}).get("trading_name") for row in observations],
                    expected_values,
                )
                self.assertEqual(
                    [row["raw_record"]["n24"] for row in observations], raw_values
                )
                row = review(quotes=[{"kind": "documentation", "quote": docs}])
                row["branch_reviews"] = [
                    {
                        "branch_id": "/" + arm,
                        "documented_condition": "Runtime empty normalization; nonempty role is documented.",
                        "expected_value_fields": ["n24"] if arm == value_arm else [],
                        "verdict": "supported",
                        "evidence_quotes": row["evidence_quotes"]
                        if arm == value_arm
                        else [],
                        "reason": "The value role requires original evidence; runtime absence does not.",
                    }
                    for arm in ("then", "else")
                ]
                runtime = ReviewRuntime([row], docs=docs)
                result = audit_name_mappings(runtime, config, records, observations)
                self.assertEqual(result["status"], "supported")
                self.assertEqual(len(runtime.requests), 1)
                host = runtime.requests[0][1]["name_mappings"][0][
                    "host_branch_evidence"
                ]
                self.assertEqual(
                    [trace["isolated_expression_result"] for trace in host["records"]],
                    expected_values,
                )
                self.assertEqual(
                    [trace["isolated_outcome"] for trace in host["records"][:3]],
                    ["abstention"] * 3,
                )
                for branch in host["branches"]:
                    proof = branch["empty_normalization"]
                    self.assertEqual(proof["source_field"], "n24")
                    self.assertEqual(
                        [
                            eval_ast(proof["equivalent_expression"], r["raw_record"])
                            for r in records
                        ],
                        expected_values,
                    )
                self.assertNotIn("MAPPER_ASSERTION", json.dumps(runtime.requests))

    def test_empty_runtime_proof_never_supplies_missing_name_role_evidence(self):
        config, records, _ = fixture()
        field = {"op": "field", "name": "a17"}
        config["execution"]["transforms"] = [
            {
                "op": "when",
                "condition": {"op": "is_present", "input": field},
                "then": {"op": "trim", "input": field},
                "else": {"op": "omit", "reason": "No source value"},
            }
        ]
        for verdict, quotes, error_kind in (
            ("uncertain", [{"kind": "header", "quote": "a17"}], "name_role_uncertain"),
            ("supported", [], "name_role_evidence_invalid"),
        ):
            with self.subTest(verdict=verdict):
                runtime = ReviewRuntime(
                    [review(verdict, quotes)], docs="a17: Display text."
                )
                result = audit_name_mappings(runtime, config, records, [])
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["errors"][0]["kind"], error_kind)
                self.assertIn(
                    "empty_normalization", result["branch_evidence"][0]["branches"][0]
                )

    def test_nonempty_discard_and_other_field_conditions_get_no_empty_proof(self):
        for condition, dependencies in (
            (
                {
                    "op": "equals",
                    "input": {"op": "field", "name": "a17"},
                    "value": "Current One Ltd",
                },
                ["a17"],
            ),
            (
                {
                    "op": "equals",
                    "input": {"op": "field", "name": "b54"},
                    "value": None,
                },
                ["a17", "b54"],
            ),
        ):
            with self.subTest(condition=condition):
                config, records, _ = fixture()
                config["field_mappings"][0]["source_fields"] = dependencies
                config["execution"]["transforms"] = [
                    {
                        "op": "when",
                        "condition": condition,
                        "then": {"op": "omit", "reason": "Claimed blank handling"},
                        "else": {"op": "trim", "input": {"op": "field", "name": "a17"}},
                    }
                ]
                row = review()
                row["branch_reviews"] = [
                    {
                        "branch_id": "/" + arm,
                        "documented_condition": "Purported runtime absence case.",
                        "expected_value_fields": [] if arm == "then" else ["a17"],
                        "verdict": "supported",
                        "evidence_quotes": []
                        if arm == "then"
                        else row["evidence_quotes"],
                        "reason": "Scripted false claim of runtime-only omission.",
                    }
                    for arm in ("then", "else")
                ]
                runtime = ReviewRuntime([row])
                result = audit_name_mappings(runtime, config, records, [])
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(
                    result["errors"][0]["kind"], "name_role_branch_invalid"
                )
                self.assertTrue(
                    all(
                        "empty_normalization" not in branch
                        for branch in result["branch_evidence"][0]["branches"]
                    )
                )

    def test_empty_proof_cannot_excuse_losing_a_documented_current_companion(self):
        config, records, _ = fixture()
        docs = "a17: Current legal name when filled; when blank, c99 holds the current legal name."
        records[1]["raw_record"]["a17"] = ""
        field = {"op": "field", "name": "a17"}
        expression = {
            "op": "when",
            "condition": {"op": "is_present", "input": field},
            "then": {"op": "trim", "input": field},
            "else": {"op": "omit", "reason": "Missing input"},
        }
        config["execution"]["transforms"] = [expression]
        self.assertIsNone(eval_ast(expression, records[1]["raw_record"]))
        self.assertEqual(records[1]["raw_record"]["c99"], "Current Two Ltd")
        row = review("contradicted", [{"kind": "documentation", "quote": docs}])
        row["branch_reviews"] = [
            {
                "branch_id": "/" + arm,
                "documented_condition": docs,
                "expected_value_fields": [field],
                "verdict": verdict,
                "evidence_quotes": row["evidence_quotes"],
                "reason": "Blank normalization is executable but cannot lose the documented current companion.",
            }
            for arm, field, verdict in (
                ("then", "a17", "supported"),
                ("else", "c99", "contradicted"),
            )
        ]
        result = audit_name_mappings(
            ReviewRuntime([row], docs=docs), config, records, []
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"][0]["kind"], "name_role_contradicted")
        self.assertIn(
            "empty_normalization", result["branch_evidence"][0]["branches"][1]
        )

    def test_complementary_names_share_review_scope_and_preserve_raw_history(self):
        config, records, _ = fixture(conditional=True)
        docs = (
            "a17: Registered legal name of the company.\n"
            + RULE
            + "\nc99: Current registered legal name of the company.\n"
        )
        first = config["field_mappings"][0]
        first.update(
            source_fields=["a17", "b54"], semantic_evidence=docs.splitlines()[0]
        )
        second = {
            **first,
            "source_field": "c99",
            "source_fields": ["c99", "b54"],
            "semantic_evidence": docs.splitlines()[2],
            "execution_pointer": "/execution/transforms/1",
        }
        config["field_mappings"].append(second)
        ast = config["execution"]["transforms"][0]
        ast["else"] = {"op": "omit", "reason": "MAPPER_ASSERTION_TO_HIDE"}
        config["execution"]["transforms"].append(
            {
                "op": "when",
                "condition": copy.deepcopy(ast["condition"]),
                "then": {"op": "omit", "reason": "MAPPER_ASSERTION_TO_HIDE"},
                "else": {"op": "field", "name": "c99"},
            }
        )
        config["source_schema"].update(
            format="json",
            data_files=["invented.json"],
            discovery_method="invented native fixture",
        )
        for field in config["source_schema"]["fields"]:
            field["inferred_type"] = "string"
        observations, report = run_engine(
            config, records, docs, {"licence": "CC BY 4.0"}, "2026-01-01T00:00:00+00:00"
        )
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            [row["entity"]["legal_name"] for row in observations],
            ["Current One Ltd", "Current Two Ltd"],
        )
        self.assertEqual(observations[1]["raw_record"]["a17"], "Former Two Ltd")

        class CurrentCoverageReviewer(ReviewRuntime):
            """Scripted original-document expectations, independent of executable reads."""

            def model(self, purpose, payload, schema):
                self.requests.append(
                    (purpose, copy.deepcopy(payload), copy.deepcopy(schema))
                )
                group = payload["name_review_contract"]["same_target_mapping_groups"][0]
                items = {
                    item["mapping_index"]: item for item in payload["name_mappings"]
                }
                # The invented original rule requires a17 on Y and c99 on null.
                # Expected fields are fixed from that rule, not scripted_branches.
                current_values_present = all(
                    any(
                        trace["source_record_id"] == record["source_record_id"]
                        and trace.get("isolated_expression_result")
                        == record["raw_record"][
                            "a17" if record["raw_record"]["b54"] == "Y" else "c99"
                        ]
                        for index in group["mapping_indices"]
                        for trace in items[index]["host_branch_evidence"]["records"]
                    )
                    for record in payload["proposal_records"]
                )
                reviews = []
                for index, item in items.items():
                    row = review(
                        "supported" if current_values_present else "contradicted",
                        index=index,
                    )
                    expected_fields = (["a17"], []) if index == 0 else ([], ["c99"])
                    row["branch_reviews"] = [
                        {
                            "branch_id": arm,
                            "documented_condition": RULE,
                            "expected_value_fields": fields,
                            "current_value_requirement": "available",
                            "current_value_fields": ["a17"]
                            if arm == "/then"
                            else ["c99"],
                            "coverage_mapping_indices": [0] if arm == "/then" else [1],
                            "verdict": row["verdict"],
                            "evidence_quotes": [
                                {"kind": "documentation", "quote": RULE}
                            ],
                            "reason": "The combined mappings must supply the documented current value.",
                        }
                        for arm, fields in zip(("/then", "/else"), expected_fields)
                    ]
                    reviews.append(row)
                response = {"reviews": reviews}
                check_response(response, schema)
                return response

        runtime = CurrentCoverageReviewer([], docs=docs)
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["status"], "supported")
        payload = runtime.requests[0][1]
        contract = payload["name_review_contract"]
        self.assertEqual(
            contract["same_target_mapping_groups"],
            [{"canonical_field": "entity.legal_name", "mapping_indices": [0, 1]}],
        )
        self.assertTrue(contract["raw_source_values_retained"])
        self.assertTrue(contract["documented_current_value_coverage_required"])
        self.assertFalse(contract["historical_name_output_required"])
        self.assertFalse(contract["additional_targets_or_observations_required"])
        self.assertEqual(
            [
                [
                    binding["source_field"]
                    for binding in item["host_semantic_evidence"]["fields"]
                ]
                for item in payload["name_mappings"]
            ],
            [["a17"], ["c99"]],
        )
        self.assertNotIn("MAPPER_ASSERTION", json.dumps(payload))
        # Omitting a historical value cannot excuse missing its documented current companion.
        for broken in ("missing_companion", "historical_value_in_companion_branch"):
            candidate = copy.deepcopy(config)
            if broken == "missing_companion":
                candidate["field_mappings"].pop()
                candidate["execution"]["transforms"].pop()
            else:
                candidate["execution"]["transforms"][1]["else"]["name"] = "a17"
                candidate["field_mappings"][1].update(
                    source_field="a17", source_fields=["a17", "b54"]
                )
            runtime = CurrentCoverageReviewer([], docs=docs)
            rejected = audit_name_mappings(runtime, candidate, records, [])
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["errors"][0]["kind"], "name_role_contradicted")
            self.assertEqual(len(runtime.requests), 2)

    def test_branch_trace_survives_an_empty_whole_config_probe(self):
        config, records, _ = fixture(conditional=True)
        runtime = ReviewRuntime([review()])
        result = audit_name_mappings(runtime, config, records, [])
        self.assertEqual(result["status"], "supported")
        evidence = runtime.requests[0][1]["name_mappings"][0]["host_branch_evidence"]
        self.assertEqual(
            [
                (b["branch_id"], b["possible_value_fields"])
                for b in evidence["branches"]
            ],
            [("/then", ["a17"]), ("/else", ["c99"])],
        )
        traces = evidence["records"]
        self.assertEqual(
            [r["isolated_expression_result"] for r in traces],
            ["Current One Ltd", "Current Two Ltd"],
        )
        self.assertEqual([r["conditions"][0]["result"] for r in traces], [True, False])
        self.assertEqual(
            [r["visited_branches"][0]["actual_value_fields"] for r in traces],
            [["a17"], ["c99"]],
        )
        self.assertTrue(all(r["actual_probe_outputs"] == [] for r in traces))
        self.assertNotIn("b54", json.dumps([r["visited_branches"] for r in traces]))

    def test_unsampled_branch_uses_ast_and_docs_without_automatic_rejection(self):
        config, records, observations = fixture(conditional=True)
        runtime = ReviewRuntime([review()])
        result = audit_name_mappings(runtime, config, records[:1], observations[:1])
        self.assertEqual(result["status"], "supported")
        branches = runtime.requests[0][1]["name_mappings"][0]["host_branch_evidence"][
            "branches"
        ]
        self.assertEqual([b["branch_id"] for b in branches], ["/then", "/else"])
        self.assertEqual(len(runtime.requests), 1)

    def test_nested_predicate_branches_are_not_name_value_branches(self):
        config, records, _ = fixture(conditional=True)
        ast = config["execution"]["transforms"][0]
        predicate = copy.deepcopy(ast["condition"])
        ast["condition"] = {
            "op": "equals",
            "input": {
                "op": "when",
                "condition": predicate,
                "then": {"op": "field", "name": "b54"},
                "else": {"op": "field", "name": "b54"},
            },
            "value": "Y",
        }
        runtime = ReviewRuntime([review()])
        result = audit_name_mappings(runtime, config, records, [])
        self.assertEqual(result["status"], "supported")
        evidence = runtime.requests[0][1]["name_mappings"][0]["host_branch_evidence"]
        self.assertEqual(
            [b["branch_id"] for b in evidence["branches"]], ["/then", "/else"]
        )
        self.assertEqual(
            [r["isolated_expression_result"] for r in evidence["records"]],
            ["Current One Ltd", "Current Two Ltd"],
        )

    def test_dispute_corrects_false_rejection_before_any_candidate_rewrite(self):
        config, records, observations = fixture(conditional=True)
        before = copy.deepcopy(config)
        with tempfile.TemporaryDirectory() as root:
            request = durable_request(root)
            wrong = {"reviews": [review("contradicted")]}
            correct = {"reviews": [review()]}
            provider = ReviewProvider([wrong, correct])
            result = audit_name_mappings(
                Runtime(request, provider), config, records, observations
            )
            self.assertEqual(result["status"], "supported")
            self.assertEqual(config, before)
            self.assertEqual(len(provider.requests), 2)
            self.assertIn("dispute_review", provider.requests[1]["payload"])
            self.assertEqual(
                result["dispute_review"]["initial_response"]["reviews"][0]["verdict"],
                "contradicted",
            )
            self.assertFalse(result["human_gold"])
            self.assertEqual(
                audit_name_mappings(
                    Runtime(request, provider), config, records, observations
                ),
                result,
            )
            self.assertEqual(len(provider.requests), 2)

    def test_unknown_dispute_outcome_is_never_resent(self):
        config, records, observations = fixture(conditional=True)
        with tempfile.TemporaryDirectory() as root:
            request = durable_request(root)
            provider = ReviewProvider(
                [
                    {"reviews": [review("contradicted")]},
                    TimeoutError("Dispute outcome unknown"),
                ]
            )
            for _ in range(2):
                with self.assertRaises(Failure) as error:
                    audit_name_mappings(
                        Runtime(request, provider), config, records, observations
                    )
                self.assertEqual(error.exception.kind, "model_outcome_unknown")
            self.assertEqual(len(provider.requests), 2)

    def test_expected_field_may_be_unmapped_but_must_not_be_falsely_supported(self):
        config, records, observations = fixture()
        branch = {
            "branch_id": "/",
            "documented_condition": "Historical row requires the documented current-name value.",
            "expected_value_fields": ["c99"],
            "verdict": "contradicted",
            "evidence_quotes": [{"kind": "header", "quote": "c99"}],
            "reason": "The actual root reads a17; c99 supplies the documented current value for this condition.",
        }
        negative = {**review("contradicted"), "branch_reviews": [branch]}
        runtime = ReviewRuntime([negative])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["errors"][0]["kind"], "name_role_contradicted")
        self.assertTrue(result["reviews"][0]["branch_analysis_verified"])
        self.assertEqual(len(runtime.requests), 2)
        positive = {
            **negative,
            "verdict": "supported",
            "branch_reviews": [{**branch, "verdict": "supported"}],
        }
        runtime = ReviewRuntime([positive])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["errors"][0]["kind"], "name_role_branch_invalid")
        self.assertEqual(len(runtime.requests), 4)
        self.assertIn(
            "predicate fields excluded",
            result["errors"][0]["branch_diagnostics"][0]["reason"],
        )

    def test_omit_explanation_is_not_exposed_as_source_evidence(self):
        config, records, _ = fixture(conditional=True)
        config["execution"]["transforms"][0]["else"] = {
            "op": "omit",
            "reason": "MAPPER_RATIONALE_CONTRADICTS_DOCUMENT",
        }
        config["field_mappings"][0]["source_fields"] = ["a17", "b54"]
        runtime = ReviewRuntime([review("uncertain")])
        audit_name_mappings(runtime, config, records, [])
        self.assertNotIn("MAPPER_RATIONALE", json.dumps(runtime.requests))

    def test_previous_review_is_not_model_context_and_retains_scope_validation(self):
        config, records, observations = fixture(conditional=True)
        original_runtime = ReviewRuntime([review()])
        first = audit_name_mappings(original_runtime, config, records, observations)
        first["reviews"][0]["reason"] = "STALE_MAPPING_CLAIM"
        runtime = ReviewRuntime([review()])
        audit_name_mappings(runtime, config, records, observations, first)
        self.assertNotIn("prior_candidate_review", runtime.requests[0][1])
        self.assertEqual(runtime.requests[0][1], original_runtime.requests[0][1])
        self.assertNotIn("STALE_MAPPING_CLAIM", json.dumps(runtime.requests))
        with self.assertRaises(Failure):
            audit_name_mappings(runtime, config, records[:1], observations[:1], first)

    def test_wrong_historical_copy_returns_actionable_error_without_rewriting(self):
        config, records, observations = fixture()
        original = copy.deepcopy((config, records, observations))
        runtime = ReviewRuntime([review("contradicted")])
        result = audit_name_mappings(runtime, config, records, observations)
        error = result["errors"][0]
        self.assertEqual(error["kind"], "name_role_contradicted")
        self.assertEqual(error["path"], "/field_mappings/0")
        self.assertEqual(error["source_fields"], ["a17"])
        self.assertIn("documented marker", error["repair_hint"])
        self.assertEqual((config, records, observations), original)
        self.assertFalse(result["human_gold"])
        # A recognized legal role does not validate historical/current selection.
        binding = runtime.requests[0][1]["name_mappings"][0]["host_semantic_evidence"][
            "fields"
        ][0]
        self.assertTrue(binding["role_check"]["supported"])

    def test_correct_conditional_preserves_both_marker_states_and_original_evidence(
        self,
    ):
        config, records, observations = fixture(conditional=True)
        self.assertEqual(
            [row["entity"]["legal_name"] for row in observations],
            ["Current One Ltd", "Current Two Ltd"],
        )
        runtime = ReviewRuntime([review()])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["errors"], [])
        purpose, payload, _ = runtime.requests[0]
        self.assertEqual(purpose, "name_role_review")
        self.assertEqual(payload["source_documentation"], DOCS)
        self.assertEqual(payload["proposal_records"], records)
        self.assertEqual(
            payload["name_mappings"][0]["executable"],
            config["execution"]["transforms"][0],
        )
        self.assertNotIn("MAPPER_SELF_ASSERTION", json.dumps(payload))
        self.assertNotIn("confidence", json.dumps(payload))

        # A real conditional remains legal in the existing generic compiler;
        # this review requires neither omission nor a particular native key.
        mapping = {
            **config["field_mappings"][0],
            "semantic_evidence": DOCS.splitlines()[0],
            "ast": config["execution"]["transforms"][0],
        }
        self.assertTrue(compile_mappings([mapping], ["a17", "b54", "c99"], DOCS))

    def test_conditional_node_alone_never_bypasses_independent_review(self):
        config, records, observations = fixture(conditional=True)
        runtime = ReviewRuntime([review("uncertain")])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["errors"][0]["kind"], "name_role_uncertain")

    def test_task_backend_supplies_its_actual_task_and_subject_for_review(self):
        config, records, observations = fixture()
        config["execution"] = {
            "kind": "llm_task",
            "task": "Return the documented current name.",
            "subject": "The registered business, not its representative.",
        }
        config["field_mappings"][0]["execution_pointer"] = "/execution/task"
        runtime = ReviewRuntime([review("uncertain")])
        result = audit_name_mappings(runtime, config, records, observations)
        payload = runtime.requests[0][1]
        self.assertEqual(
            payload["name_mappings"][0]["executable"], config["execution"]["task"]
        )
        self.assertEqual(payload["execution_subject"], config["execution"]["subject"])
        self.assertEqual(result["errors"][0]["kind"], "name_role_uncertain")

    def test_ordinary_single_legal_name_can_use_exact_native_header_evidence(self):
        config, records, observations = fixture()
        config["field_mappings"][0]["source_field"] = "Registered legal name"
        config["field_mappings"][0]["source_fields"] = ["Registered legal name"]
        config["source_schema"]["fields"] = [{"source_field": "Registered legal name"}]
        config["execution"]["transforms"][0]["name"] = "Registered legal name"
        runtime = ReviewRuntime(
            [review(quotes=[{"kind": "header", "quote": "Registered legal name"}])],
            docs="",
        )
        self.assertEqual(
            audit_name_mappings(runtime, config, records, observations)["errors"], []
        )

    def test_supported_verdict_without_exact_evidence_is_not_accepted(self):
        config, records, observations = fixture()
        for quotes in (
            [],
            [{"kind": "documentation", "quote": "invented source rule"}],
            [{"kind": "header", "quote": "c99"}],
        ):
            with self.subTest(quotes=quotes):
                runtime = ReviewRuntime([review(quotes=quotes)])
                errors = audit_name_mappings(runtime, config, records, observations)[
                    "errors"
                ]
                self.assertEqual(errors[0]["kind"], "name_role_evidence_invalid")

    def test_bad_quote_rejects_review_but_keeps_original_semantic_repair(self):
        config, records, observations = fixture()
        runtime = ReviewRuntime(
            [
                review(
                    "contradicted",
                    [{"kind": "documentation", "quote": "invented source rule"}],
                )
            ]
        )
        result = audit_name_mappings(runtime, config, records, observations)
        error = result["errors"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(error["kind"], "name_role_evidence_invalid")
        self.assertEqual(error["review_verdict"], "contradicted")
        self.assertIn("original flag rule", error["message"])
        self.assertIn("Use the documented marker", error["repair_hint"])
        self.assertIn("exact contiguous original", error["repair_hint"])
        self.assertFalse(result["reviews"][0]["evidence_verified"])
        self.assertEqual(error["refs"], [])

    def test_bad_quote_is_corrected_by_reviewer_and_reused_after_resume(self):
        config, records, observations = fixture(conditional=True)
        bad = {
            "reviews": [
                review(
                    quotes=[{"kind": "documentation", "quote": "invented source rule"}]
                )
            ]
        }
        good = {"reviews": [review()]}
        with tempfile.TemporaryDirectory() as root:
            request = durable_request(root)
            provider = ReviewProvider([bad, good])
            result = audit_name_mappings(
                Runtime(request, provider), config, records, observations
            )
            self.assertEqual(result["status"], "supported")
            self.assertTrue(result["reviews"][0]["evidence_verified"])
            self.assertEqual(len(result["evidence_corrections"]), 1)
            self.assertEqual(len(provider.requests), 2)
            correction = provider.requests[1]["payload"]["evidence_correction"]
            self.assertEqual(
                correction["previous_response"],
                scripted_branches(bad, provider.requests[0]["payload"]),
            )
            self.assertEqual(
                correction["diagnostics"][0]["quote"], "invented source rule"
            )
            self.assertEqual(correction["diagnostics"][0]["quote_index"], 0)
            self.assertIn("exact contiguous", correction["diagnostics"][0]["reason"])
            for sent in provider.requests:
                self.assertNotIn("MAPPER_SELF_ASSERTION", json.dumps(sent["payload"]))
                self.assertNotIn("confidence", json.dumps(sent["payload"]))
            again = audit_name_mappings(
                Runtime(request, provider), config, records, observations
            )
            self.assertEqual(again, result)
            # A mapper-only wording change also reuses the corrected independent
            # review, rather than forcing a new paid call or reviving bad quotes.
            config["field_mappings"][0]["semantic_evidence"] = "a17"
            config["field_mappings"][0]["transformation"] = "Different mapper wording."
            changed = audit_name_mappings(
                Runtime(request, provider), config, records, observations
            )
            self.assertEqual(changed["reviews"], result["reviews"])
            self.assertEqual(changed["status"], "supported")
            self.assertEqual(len(provider.requests), 2)
            calls = Runtime(request, provider).run.state["calls"]
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call["status"] == "completed" for call in calls))

    def test_repeated_bad_quotes_exhaust_two_corrections_and_remain_rejected(self):
        config, records, observations = fixture()
        bad = {
            "reviews": [
                review(
                    "contradicted",
                    [{"kind": "documentation", "quote": "invented source rule"}],
                )
            ]
        }
        with tempfile.TemporaryDirectory() as root:
            request = durable_request(root)
            provider = ReviewProvider([copy.deepcopy(bad) for _ in range(4)])
            result = audit_name_mappings(
                Runtime(request, provider), config, records, observations
            )
            self.assertEqual(len(provider.requests), 4)
            self.assertEqual(
                [item["attempt"] for item in result["evidence_corrections"]], [1, 2]
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["errors"][0]["kind"], "name_role_evidence_invalid")
            self.assertIn(
                "Use the documented marker", result["errors"][0]["repair_hint"]
            )
            self.assertEqual(
                audit_name_mappings(
                    Runtime(request, provider), config, records, observations
                ),
                result,
            )
            self.assertEqual(len(provider.requests), 4)

    def test_unknown_evidence_correction_is_never_resent_on_resume(self):
        config, records, observations = fixture(conditional=True)
        bad = {
            "reviews": [review(quotes=[{"kind": "header", "quote": "invented_header"}])]
        }
        with tempfile.TemporaryDirectory() as root:
            request = durable_request(root)
            provider = ReviewProvider(
                [bad, TimeoutError("Correction may have completed remotely")]
            )
            for _ in range(2):
                with self.assertRaises(Failure) as error:
                    audit_name_mappings(
                        Runtime(request, provider), config, records, observations
                    )
                self.assertEqual(error.exception.kind, "model_outcome_unknown")
            self.assertEqual(len(provider.requests), 2)
            calls = Runtime(request, provider).run.state["calls"]
            self.assertEqual(
                [call["status"] for call in calls],
                ["completed", "model_outcome_unknown"],
            )

    def test_corrected_quote_keeps_semantic_verdict_and_historical_repair_guidance(
        self,
    ):
        config, records, observations = fixture()
        for verdict in ("contradicted", "uncertain"):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as root:
                bad = {
                    "reviews": [
                        review(
                            verdict,
                            [
                                {
                                    "kind": "documentation",
                                    "quote": "invented source rule",
                                }
                            ],
                        )
                    ]
                }
                corrected = {"reviews": [review(verdict)]}
                request = durable_request(root)
                provider = ReviewProvider([bad, corrected, corrected])
                result = audit_name_mappings(
                    Runtime(request, provider), config, records, observations
                )
                self.assertEqual(result["status"], "rejected")
                self.assertTrue(result["reviews"][0]["evidence_verified"])
                self.assertEqual(result["errors"][0]["kind"], "name_role_" + verdict)
                self.assertIn(
                    "Use the documented marker", result["errors"][0]["repair_hint"]
                )
                self.assertEqual(
                    provider.requests[1]["payload"]["evidence_correction"][
                        "previous_response"
                    ],
                    scripted_branches(bad, provider.requests[0]["payload"]),
                )

    def test_bad_execution_pointer_returns_feedback_without_model_call(self):
        config, records, observations = fixture()
        config["field_mappings"][0]["execution_pointer"] = "/execution/transforms/9"
        runtime = ReviewRuntime([review()])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["status"], "local_validation_failed")
        self.assertEqual(result["errors"][0]["kind"], "name_role_execution_invalid")
        self.assertIn("execution_pointer", result["errors"][0]["repair_hint"])
        self.assertEqual(runtime.requests, [])

    def test_every_name_mapping_reviewed_once_even_if_indices_noncontiguous(self):
        config, records, observations = fixture()
        name = config["field_mappings"][0]
        config["field_mappings"] = [
            {**name, "canonical_field": "entity.status"},
            name,
            {**name, "canonical_field": "entity.trading_name"},
        ]
        runtime = ReviewRuntime([review(index=1), review(index=1)])
        errors = audit_name_mappings(runtime, config, records, observations)["errors"]
        self.assertEqual([error["mapping_index"] for error in errors], [1, 2])
        self.assertTrue(
            all(error["kind"] == "name_role_review_invalid" for error in errors)
        )
        runtime = ReviewRuntime([review(index=0), review(index=2)])
        with self.assertRaises(Failure):
            audit_name_mappings(runtime, config, records, observations)

    def test_no_names_needs_no_review_call(self):
        config, records, observations = fixture()
        config["field_mappings"][0]["canonical_field"] = "entity.status"
        runtime = ReviewRuntime([])
        result = audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["status"], "no_name_mapping")
        self.assertEqual(runtime.requests, [])

    def test_probe_outputs_cannot_introduce_other_records(self):
        config, records, observations = fixture()
        observations[0]["source_record_id"] = "held-out-row"
        runtime = ReviewRuntime([review()])
        with self.assertRaises(Failure):
            audit_name_mappings(runtime, config, records, observations)
        self.assertEqual(runtime.requests, [])


if __name__ == "__main__":
    unittest.main()
