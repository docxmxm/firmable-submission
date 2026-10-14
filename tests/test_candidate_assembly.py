"""Small-task assembly and recovery are host contracts, not mapping evaluations."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.agent import (
    MAX_DECISIONS,
    decision_schema,
    onboard_source,
    validate_decision,
)
from firmable_runtime.candidate_assembly import (
    TASK_CAPABILITIES,
    TASK_SCHEMA,
    assemble_config,
    build_supported_subset,
    generate_candidate,
    manifest_schema,
    task_bindings,
    task_budget,
    task_schema,
)
from firmable_runtime.common import Failure, canonical, digest
from firmable_runtime.execution import CANONICAL_FIELDS, run_engine
from firmable_runtime.openrouter_wire import OpenRouterWireContract
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from test_agent import NativeFixture, ScriptedProvider, candidate_response, control
from test_llm import (
    Fixture as LlmFixture,
    ScriptedProvider as LlmProvider,
    extracted,
    task_config,
    verified,
)


def parts(config):
    manifest = candidate_response({"purpose": "candidate_manifest"}, config)
    slots = ["mapping:" + str(i) for i in range(len(config["field_mappings"]))]
    slots += ["time:" + item["slot"] for item in manifest.get("time_mappings", [])]
    rules = candidate_response(
        {"purpose": "candidate_rules", "payload": {"expression_slots": slots}}, config
    )
    return manifest, rules


def llm_parts(count=1, field="Statement"):
    config = task_config()
    template = config["field_mappings"][0]
    targets = ["entity.legal_name", *sorted(CANONICAL_FIELDS - {"entity.legal_name"})]
    config["field_mappings"] = [
        {
            **copy.deepcopy(template),
            "canonical_field": target,
            "source_field": field,
            "source_fields": [field],
        }
        for target in targets[:count]
    ]
    return (
        candidate_response({"purpose": "candidate_manifest"}, config),
        candidate_response({"purpose": "candidate_task"}, config),
    )


class AssemblyTests(unittest.TestCase):
    def test_subset_preserves_expression_metadata_time_and_shared_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            config = NativeFixture(root).config()
            config["source_schema"] = {"fields": []}
            second = copy.deepcopy(config["field_mappings"][0])
            second.update(
                canonical_field="entity.trading_name",
                execution_pointer="/execution/transforms/1",
            )
            config["field_mappings"].append(second)
            config["execution"]["transforms"].append(
                {"op": "trim", "input": {"op": "field", "name": second["source_field"]}}
            )
            config["execution"]["observed_time"] = {
                "ast": {"op": "field", "name": "stamp"},
                "source_fields": ["stamp"],
                "evidence_ref": "stamp",
            }
            original = copy.deepcopy(config)
            result = build_supported_subset(config, [0])
            self.assertEqual(config, original)
            expected = copy.deepcopy(second)
            expected["execution_pointer"] = "/execution/transforms/0"
            expected["validation_pass"] = False
            self.assertEqual(result["field_mappings"], [expected])
            self.assertEqual(
                result["execution"]["transforms"],
                [original["execution"]["transforms"][1]],
            )
            self.assertEqual(
                result["execution"]["observed_time"],
                original["execution"]["observed_time"],
            )
            self.assertEqual(result["source_schema"], original["source_schema"])
            self.assertEqual(result["unmapped_fields"], original["unmapped_fields"])
            config["field_mappings"][0]["source_fields"].append("only_blocked")
            result = build_supported_subset(config, [0])
            self.assertEqual(
                result["unmapped_fields"][-1]["source_field"], "only_blocked"
            )

    def test_subset_rejects_partial_target_groups_and_invalid_removal(self):
        with tempfile.TemporaryDirectory() as root:
            config = NativeFixture(root).config()
            config["source_schema"] = {"fields": []}
            config["field_mappings"].append(copy.deepcopy(config["field_mappings"][0]))
            for blocked in ([], [True], [-1], [3], [0], [0, 1]):
                with self.assertRaises(Failure):
                    build_supported_subset(config, blocked)
            config["execution"]["kind"] = "llm_task"
            with self.assertRaises(Failure):
                build_supported_subset(config, [0])

    def test_unknown_abstention_is_only_for_supported_enum_targets(self):
        with tempfile.TemporaryDirectory() as root:
            config = NativeFixture(root).config()
            manifest, _ = parts(config)
            manifest.pop("time_mappings")
            manifest["field_mappings"][0]["claim_kind"] = "unknown_abstention"
            task = {
                "tasks": [
                    {"slot": "mapping:0", "instructions": "Emit no unsupported name"}
                ],
                "subject": "Source entity",
            }
            for target in ("entity.legal_name", "entity.trading_name", "entity.abn"):
                manifest["field_mappings"][0]["canonical_field"] = target
                with self.assertRaises(Failure) as caught:
                    assemble_config(manifest, task, "llm_task", {"fields": []})
                self.assertEqual(
                    caught.exception.details["reason_code"],
                    "unsupported_unknown_abstention",
                )
                self.assertEqual(caught.exception.details["canonical_field"], target)
            manifest["field_mappings"][0]["canonical_field"] = "entity.status"
            assembled = assemble_config(manifest, task, "llm_task", {"fields": []})
            self.assertEqual(
                assembled["field_mappings"][0]["claim_kind"], "unknown_abstention"
            )

    def test_control_is_closed_and_unused_fields_must_be_empty(self):
        for action in (
            control("probe", candidate_id="one", backend="ast"),
            control("select", candidate_id="one"),
            control("inspect_field_evidence", field="title"),
            control("stop", reason="No supported mapping"),
        ):
            validate_decision(action)
            unused = next(
                key
                for key in ("reason", "field", "backend", "candidate_id")
                if action[key] == ""
            )
            with self.assertRaises(Failure):
                validate_decision(
                    {**action, unused: "ast" if unused == "backend" else "unused"}
                )
        with self.assertRaises(Failure):
            check_response(
                {**control("probe", candidate_id="one", backend="ast"), "config": {}},
                decision_schema(),
            )
        with self.assertRaises(Failure):
            validate_decision(control("probe", candidate_id="one"))

    def test_host_assembles_shuffled_rules_and_all_time_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            config = fixture.config()
            for slot in ("observed_time", "valid_from", "valid_to"):
                config["execution"][slot] = {
                    "ast": {"op": "parse_date", "input": {"op": "field", "name": slot}},
                    "source_fields": [slot],
                    "evidence_ref": slot,
                }
            manifest, rules = parts(config)
            rules["expressions"].reverse()
            original = copy.deepcopy((manifest, rules))
            source_schema = {
                "fields": [
                    {"source_field": field}
                    for field in (
                        fixture.field,
                        "observed_time",
                        "valid_from",
                        "valid_to",
                    )
                ]
            }
            assembled = assemble_config(manifest, rules, "ast", source_schema)
            self.assertEqual(assembled["execution"], config["execution"])
            self.assertEqual(assembled["source_schema"], source_schema)
            self.assertEqual(
                assembled["field_mappings"][0]["execution_pointer"],
                "/execution/transforms/0",
            )
            self.assertIs(assembled["field_mappings"][0]["validation_pass"], False)
            self.assertEqual((manifest, rules), original)

    def test_native_time_refs_compile_and_evaluate_without_weakening_validity(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            config = fixture.config()
            time_fields = {
                "observed_time": "stamp_z",
                "valid_from": "start_z",
                "valid_to": "end_z",
            }
            for slot, field in time_fields.items():
                config["execution"][slot] = {
                    "ast": {
                        "op": "parse_datetime"
                        if slot == "observed_time"
                        else "parse_date",
                        "input": {"op": "field", "name": field},
                    },
                    "source_fields": [field],
                    "evidence_ref": field,
                }
            docs = (
                fixture.definition
                + "\nrow_note: Internal row reference.\nstamp_z: Timestamp when the source stated this record.\nstart_z: Validity start date for all claims in this record.\nend_z: Validity end date for all claims in this record.\n"
            )
            fields = [fixture.field, "row_note", *time_fields.values()]
            source_schema = {
                "format": "json",
                "data_files": ["invented.json"],
                "discovery_method": "invented native fixture",
                "fields": [
                    {"source_field": field, "inferred_type": "string"}
                    for field in fields
                ],
            }
            manifest, rules = parts(config)
            assembled = assemble_config(manifest, rules, "ast", source_schema)
            raw = {
                fixture.field: "Aster Pty Ltd",
                "row_note": "r1",
                "stamp_z": "2026-01-02T03:04:05+00:00",
                "start_z": "2025-06-01",
                "end_z": "2026-06-01",
            }
            records = [
                {
                    "source_id": "invented-clock-register",
                    "source_record_id": "r1",
                    "raw_record": raw,
                }
            ]
            observations, report = run_engine(
                assembled,
                records,
                docs,
                {"licence": "CC BY 4.0"},
                "2026-01-03T00:00:00+00:00",
            )
            self.assertEqual(report["errors"], [])
            self.assertEqual(observations[0]["observed_at"], raw["stamp_z"])
            self.assertEqual(
                observations[0]["observed_at_basis"]["evidence_ref"], "stamp_z"
            )
            self.assertEqual(observations[0]["valid_from"], raw["start_z"])
            self.assertEqual(observations[0]["valid_to"], raw["end_z"])
            self.assertEqual(observations[0]["raw_record"], raw)
            with self.assertRaises(Failure) as caught:
                run_engine(
                    assembled,
                    records,
                    docs.replace(
                        "Validity start date for all claims in this record",
                        "Date on which the company was registered",
                    ),
                    {"licence": "CC BY 4.0"},
                    "2026-01-03T00:00:00+00:00",
                )
            self.assertIn(
                "validity_ownership",
                [item["code"] for item in caught.exception.details["findings"]],
            )
            manifest["time_mappings"][0]["evidence_ref"] = (
                "Timestamp when the source stated this record"
            )
            with self.assertRaises(Failure) as caught:
                assemble_config(manifest, rules, "ast", source_schema)
            self.assertEqual(caught.exception.details["reason_code"], "time_evidence")

    def test_missing_duplicate_and_unknown_slots_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            config = NativeFixture(root).config()
            config["field_mappings"].append(copy.deepcopy(config["field_mappings"][0]))
            manifest, rules = parts(config)
            variants = [
                {"expressions": rules["expressions"][:1]},
                {"expressions": [rules["expressions"][0]] * 2},
                {
                    "expressions": [
                        rules["expressions"][0],
                        {**rules["expressions"][1], "slot": "mapping:9"},
                    ]
                },
            ]
            for broken in variants:
                with self.subTest(broken=broken), self.assertRaises(Failure):
                    assemble_config(manifest, broken, "ast", {})

    def test_manifest_limits_time_uniqueness_and_host_owned_fields(self):
        with tempfile.TemporaryDirectory() as root:
            manifest, rules = parts(NativeFixture(root).config())
            for key, value in (("source_schema", {}), ("execution", {})):
                with self.assertRaises(Failure):
                    assemble_config({**manifest, key: value}, rules, "ast", {})
            for key, value in (
                ("execution_pointer", "/execution/transforms/0"),
                ("validation_pass", True),
            ):
                broken = copy.deepcopy(manifest)
                broken["field_mappings"][0][key] = value
                with self.assertRaises(Failure):
                    check_response(broken, manifest_schema("ast"))
            for time_entries in (
                [
                    {
                        "slot": "observed_time",
                        "source_fields": ["date"],
                        "evidence_ref": "date",
                    }
                ]
                * 2,
                [
                    {
                        "slot": "invented",
                        "source_fields": ["date"],
                        "evidence_ref": "date",
                    }
                ],
            ):
                with self.assertRaises(Failure):
                    assemble_config(
                        {**manifest, "time_mappings": time_entries}, rules, "ast", {}
                    )
            with self.assertRaises(Failure):
                check_response(
                    {**manifest, "field_mappings": manifest["field_mappings"] * 81},
                    manifest_schema("ast"),
                )

    def test_llm_task_assembly_preserves_the_original_backend_contract(self):
        with tempfile.TemporaryDirectory() as root:
            config = NativeFixture(root).config()
            config["execution"] = {
                "kind": "llm_task",
                "task": "Extract documented names using raw spans.",
                "subject": "The registered company.",
            }
            manifest = candidate_response({"purpose": "candidate_manifest"}, config)
            task = candidate_response({"purpose": "candidate_task"}, config)
            original = copy.deepcopy((manifest, task))
            assembled = assemble_config(manifest, task, "llm_task", {})
            execution = assembled["execution"]
            self.assertEqual(set(execution), {"kind", "task", "subject"})
            self.assertEqual(execution["kind"], config["execution"]["kind"])
            self.assertEqual(execution["subject"], task["subject"])
            bound = json.loads(execution["task"])
            self.assertEqual(
                bound["mappings"],
                [
                    {
                        **task_bindings(manifest)[0],
                        "semantic_instructions": task["tasks"][0]["instructions"],
                    }
                ],
            )
            self.assertEqual((manifest, task), original)
            self.assertEqual(
                assembled["field_mappings"][0]["execution_pointer"], "/execution/task"
            )
            self.assertIs(assembled["field_mappings"][0]["validation_pass"], False)
            with self.assertRaises(Failure):
                assemble_config(manifest, {**task, "source_schema": {}}, "llm_task", {})

    def test_llm_task_slots_bind_targets_and_dependencies_despite_conflicting_prose(
        self,
    ):
        config = task_config()
        second = copy.deepcopy(config["field_mappings"][0])
        second.update(
            canonical_field="entity.trading_name",
            source_field="Alias",
            source_fields=["Alias", "CurrentFlag"],
        )
        config["field_mappings"].append(second)
        manifest = candidate_response({"purpose": "candidate_manifest"}, config)
        task = {
            "subject": "The company described by the record",
            "tasks": [
                {
                    "slot": "mapping:1",
                    "instructions": "Use the documented current alias.",
                },
                {
                    "slot": "mapping:0",
                    "instructions": "Extract entity.abn from Other. Do not emit entity.legal_name.",
                },
            ],
        }
        assembled = assemble_config(manifest, task, "llm_task", {})
        bound = json.loads(assembled["execution"]["task"])
        self.assertIn("not scope authority", bound["scope_authority"])
        self.assertEqual(bound["execution_capabilities"], TASK_CAPABILITIES)
        self.assertEqual(
            [
                {key: item[key] for key in task_bindings(manifest)[0]}
                for item in bound["mappings"]
            ],
            task_bindings(manifest),
        )
        self.assertEqual(
            [item["semantic_instructions"] for item in bound["mappings"]],
            [task["tasks"][1]["instructions"], task["tasks"][0]["instructions"]],
        )
        for original, actual in zip(
            manifest["field_mappings"], assembled["field_mappings"]
        ):
            self.assertEqual({key: actual[key] for key in original}, original)
            self.assertIs(actual["validation_pass"], False)

    def test_llm_task_rejects_scope_keys_and_missing_duplicate_or_unknown_slots(self):
        config = task_config()
        second = copy.deepcopy(config["field_mappings"][0])
        second["canonical_field"] = "entity.trading_name"
        config["field_mappings"].append(second)
        manifest = candidate_response({"purpose": "candidate_manifest"}, config)
        task = candidate_response({"purpose": "candidate_task"}, config)
        variants = []
        for key, value in (
            ("canonical_field", "entity.abn"),
            ("source_field", "Other"),
            ("source_fields", ["Other"]),
        ):
            broken = copy.deepcopy(task)
            broken["tasks"][0][key] = value
            variants.append(broken)
        variants.extend(
            [
                {**task, "tasks": task["tasks"][:1]},
                {**task, "tasks": task["tasks"] + [task["tasks"][0]]},
                {**task, "tasks": [task["tasks"][0]] * 2},
                {
                    **task,
                    "tasks": [
                        task["tasks"][0],
                        {"slot": "mapping:9", "instructions": "Unsupported slot"},
                    ],
                },
                {"task": "Old unbound task", "subject": task["subject"]},
            ]
        )
        for broken in variants:
            with self.subTest(broken=broken), self.assertRaises(Failure):
                assemble_config(manifest, broken, "llm_task", {})

    def test_llm_task_transport_is_small_and_roundtrips_host_bound_slots(self):
        base = OpenRouterWireContract(TASK_SCHEMA)
        self.assertEqual(
            base.complexity, {"optional_parameters": 0, "union_parameters": 0}
        )
        for count in (1, 15):
            manifest, _ = llm_parts(count)
            value = {
                "tasks": [
                    {
                        "slot": "mapping:" + str(index),
                        "instructions": "Preserve original documented meaning.",
                    }
                    for index in range(count)
                ],
                "subject": "The company described by the record",
            }
            with self.subTest(count=count):
                wire = OpenRouterWireContract(task_schema(manifest))
                self.assertEqual(wire.decode(wire.encode(value)), value)
                self.assertEqual(
                    wire.complexity, {"optional_parameters": 0, "union_parameters": 0}
                )
                self.assertFalse(wire.ast_paths)
                self.assertLess(len(canonical(wire.schema)), 1500)

    def test_llm_task_budget_fits_one_and_fifteen_slots_without_truncating(self):
        for count in (1, 15):
            # Non-ASCII field names count as characters, not their UTF-8 byte length.
            for field in ("Statement", "Å" * 30):
                with self.subTest(count=count, field_length=len(field)):
                    manifest, task = llm_parts(count, field)
                    budget = task_budget(manifest)
                    limit = budget["instruction_characters_per_slot"]
                    for item in task["tasks"]:
                        item["instructions"] = "x" * limit
                    assembled = assemble_config(manifest, task, "llm_task", {})
                    serialized = assembled["execution"]["task"]
                    self.assertLessEqual(len(serialized), budget["max_task_characters"])
                    self.assertLess(
                        budget["max_task_characters"] - len(serialized), count
                    )
                    self.assertEqual(
                        [
                            item["semantic_instructions"]
                            for item in json.loads(serialized)["mappings"]
                        ],
                        [item["instructions"] for item in task["tasks"]],
                    )
                    task["tasks"][0]["instructions"] += "x"
                    with self.assertRaises(Failure):
                        assemble_config(manifest, task, "llm_task", {})

    def test_llm_task_budget_counts_json_escaping_at_the_final_boundary(self):
        manifest, task = llm_parts(field='Native "label"\n')
        budget = task_budget(manifest)
        available = budget["escaped_instruction_characters_total"]
        for escaped in ('"', "\\", "\n", "\t", "\x01"):
            with self.subTest(escaped=repr(escaped)):
                escape_length = len(canonical(escaped).decode()) - 2
                repeats, remainder = divmod(available, escape_length)
                instructions = escaped * repeats + "x" * remainder
                task["tasks"][0]["instructions"] = instructions
                assembled = assemble_config(manifest, task, "llm_task", {})
                self.assertEqual(
                    len(assembled["execution"]["task"]), budget["max_task_characters"]
                )
                self.assertEqual(
                    json.loads(assembled["execution"]["task"])["mappings"][0][
                        "semantic_instructions"
                    ],
                    instructions,
                )
                task["tasks"][0]["instructions"] += "x"
                check_response(task, task_schema(manifest))
                with self.assertRaises(Failure) as caught:
                    assemble_config(manifest, task, "llm_task", {})
                self.assertEqual(
                    caught.exception.details["reason_code"], "task_text_too_long"
                )
                self.assertEqual(
                    caught.exception.details["serialized_task_characters"], 4097
                )


class BoundTaskRouteTests(LlmFixture):
    def test_conflicting_task_does_not_authorize_extra_targets_or_dependencies(self):
        (self.evidence / "data.csv").write_text(
            "Statement,Other\nRegistered as: One Test Pty Ltd,Other Test Pty Ltd\n"
        )
        (self.evidence / "source.txt").write_text(
            "Statement: Registered legal name of the company described by this record.\n"
            "Other: Internal free text."
        )
        manifest = candidate_response({"purpose": "candidate_manifest"}, task_config())
        manifest["unmapped_fields"] = [
            {"source_field": "Other", "reason": "No supported claim"}
        ]
        task = {
            "tasks": [
                {
                    "slot": "mapping:0",
                    "instructions": "Emit entity.trading_name from Other instead.",
                }
            ],
            "subject": "The company described by the record",
        }
        config = assemble_config(manifest, task, "llm_task", {})
        for mode, code in (
            ("target", "undeclared_or_duplicate_claim"),
            ("dependency", "foreign_field"),
            ("fabrication", "value_not_in_raw"),
            ("owner", "model_semantic_contradicted"),
            ("valid", None),
        ):

            def reply(request):
                if request["purpose"] == "llm_extract":
                    value = extracted(request)
                    claim = value["records"][0]["claims"][0]
                    if mode == "target":
                        claim["canonical_field"] = "entity.trading_name"
                    elif mode == "dependency":
                        claim["value"] = "Other Test Pty Ltd"
                        claim["raw_refs"] = [{"field": "Other", "whole_field": True}]
                    elif mode == "fabrication":
                        claim["value"] = "Invented Test Pty Ltd"
                    return value
                value = verified(request)
                if mode == "owner":
                    value["reviews"][0].update(
                        verdict="contradicted",
                        reason="The value belongs to another subject.",
                    )
                return value

            with self.subTest(mode=mode):
                runtime, selected, rows = self.context(LlmProvider(reply), config)
                observations, report = self.run_route(runtime, selected, rows)
                if code:
                    self.assertEqual(
                        [item["reason_code"] for item in report["errors"]], [code]
                    )
                    self.assertNotIn("entity", observations[0])
                    self.assertEqual(
                        observations[0]["raw_record"], rows[0]["raw_record"]
                    )
                else:
                    self.assertEqual(report["errors"], [])
                    self.assertEqual(
                        observations[0]["entity"]["legal_name"], "One Test Pty Ltd"
                    )


class AssemblyRecoveryTests(unittest.TestCase):
    def test_llm_generation_supplies_bound_scope_and_reuses_its_manifest_checkpoint(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            config = fixture.config()
            config["execution"] = {
                "kind": "llm_task",
                "task": "Extract the documented legal name of this subject.",
                "subject": "The registered company.",
            }
            provider = ScriptedProvider(fixture)
            provider.candidate_configs["task"] = config
            runtime = Runtime(fixture.request, provider)
            context = {
                "source_schema": {"fields": [{"source_field": fixture.field}]},
                "proposal_records": runtime.tables[0]["records"][:2],
            }
            assembled = generate_candidate(runtime, "task", "llm_task", 1, context)
            self.assertEqual(
                [request["purpose"] for request in provider.requests],
                ["candidate_manifest", "candidate_task"],
            )
            request = provider.requests[-1]
            manifest = request["payload"]["manifest"]
            self.assertEqual(
                request["payload"]["task_bindings"], task_bindings(manifest)
            )
            self.assertEqual(
                request["payload"]["execution_capabilities"], TASK_CAPABILITIES
            )
            self.assertEqual(request["payload"]["task_budget"], task_budget(manifest))
            self.assertEqual(request["output_schema"], task_schema(manifest))
            saved = runtime.run.load_artifact("candidate-execution-task")
            self.assertEqual(saved["manifest_hash"], digest(manifest))
            self.assertEqual(
                generate_candidate(runtime, "task", "llm_task", 1, context), assembled
            )
            self.assertEqual(len(provider.requests), 2)

    def test_llm_oversized_binding_fails_locally_before_task_generation(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root, field="F" * 2100)
            config = fixture.config()
            config["execution"] = {
                "kind": "llm_task",
                "task": "Extract the source-supported name.",
                "subject": "The registered company.",
            }
            provider = ScriptedProvider(fixture, ["good", "stop"])
            with patch.object(fixture, "config", return_value=config):
                result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            feedback = provider.requests[-1]["payload"]["feedback"]
            self.assertEqual(feedback["error"], "candidate_assembly_invalid")
            self.assertEqual(feedback["reason_code"], "task_binding_too_large")
            self.assertGreater(feedback["host_binding_characters"], 4096)
            self.assertEqual(
                [request["purpose"] for request in provider.requests],
                [
                    "inspect",
                    "agent_decision",
                    "candidate_manifest",
                    "agent_decision",
                ],
            )
            runtime = Runtime(fixture.request, provider)
            self.assertNotIn("candidate-execution-good", runtime.run.state["artifacts"])
            self.assertIn("new candidate_id", feedback["repair_hint"])

    def test_duplicate_llm_target_rejected_before_task_call_and_returned_as_feedback(
        self,
    ):
        class DuplicateTaskFixture(NativeFixture):
            def config(self, bad=False):
                config = super().config()
                config["field_mappings"].append(
                    copy.deepcopy(config["field_mappings"][0])
                )
                config["execution"] = {
                    "kind": "llm_task",
                    "task": "Extract the documented name.",
                    "subject": "The registered company.",
                }
                return config

        with tempfile.TemporaryDirectory() as root:
            fixture = DuplicateTaskFixture(root)
            provider = ScriptedProvider(fixture, ["good", "stop"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            purposes = [request["purpose"] for request in provider.requests]
            self.assertEqual(
                purposes,
                [
                    "inspect",
                    "agent_decision",
                    "candidate_manifest",
                    "agent_decision",
                ],
            )
            feedback = provider.requests[-1]["payload"]["feedback"]
            self.assertEqual(feedback["error"], "candidate_assembly_invalid")
            self.assertEqual(feedback["reason_code"], "duplicate_llm_canonical_mapping")
            self.assertIn("combine source fields", feedback["message"])
            self.assertIn("new candidate_id", feedback["repair_hint"])
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), 4)

    def test_unknown_manifest_or_rules_outcome_stops_and_is_never_resent(self):
        for purpose, expected_calls in (
            ("candidate_manifest", 3),
            ("candidate_rules", 5),
        ):

            class InterruptedProvider(ScriptedProvider):
                def generate(self, request, call_id):
                    if request["purpose"] == purpose:
                        self.requests.append(copy.deepcopy(request))
                        raise TimeoutError("Small task may have been processed")
                    return super().generate(request, call_id)

            with self.subTest(purpose=purpose), tempfile.TemporaryDirectory() as root:
                fixture = NativeFixture(root)
                provider = InterruptedProvider(fixture, ["good", "select"])
                for _ in range(2):
                    with self.assertRaises(Failure) as caught:
                        onboard_source(fixture.request, provider)
                    self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                self.assertEqual(len(provider.requests), expected_calls)
                self.assertEqual(provider.actions, ["select"])

    def test_resume_after_each_small_task_artifact_reuses_the_response(self):
        for stage, saved_first in (
            ("candidate-manifest-good", True),
            ("candidate-manifest-good", False),
            ("candidate-discovery-good", True),
            ("candidate-discovery-good", False),
            ("candidate-execution-good", True),
            ("candidate-execution-good", False),
        ):
            with (
                self.subTest(stage=stage, saved_first=saved_first),
                tempfile.TemporaryDirectory() as root,
            ):
                fixture = NativeFixture(root)
                provider = ScriptedProvider(fixture, ["good", "select"])
                original = Runtime.artifact

                def interrupt(runtime, name, value):
                    if name == stage and not saved_first:
                        raise RuntimeError(
                            "Crash after journal completion before task artifact"
                        )
                    result = original(runtime, name, value)
                    if name == stage:
                        raise RuntimeError("Crash after small task artifact")
                    return result

                with (
                    patch.object(Runtime, "artifact", interrupt),
                    self.assertRaises(RuntimeError),
                ):
                    onboard_source(fixture.request, provider)
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(provider.requests), 7)

    def test_manifest_hash_change_is_rejected_before_execution_reuse(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            onboard_source(fixture.request, provider)
            runtime = Runtime(fixture.request, provider)
            context = runtime.run.load_artifact("candidate-context-good")["context"]
            original = runtime.run.load_artifact

            def changed_manifest(name):
                value = original(name)
                if name == "candidate-manifest-good":
                    value["response"]["field_mappings"][0]["confidence"] = 0.1
                return value

            with (
                patch.object(runtime.run, "load_artifact", changed_manifest),
                self.assertRaises(Failure) as caught,
            ):
                generate_candidate(runtime, "good", "ast", 0, context)
            self.assertEqual(caught.exception.kind, "checkpoint_failure")
            self.assertEqual(len(provider.requests), 7)
            changed = copy.deepcopy(context)
            changed["proposal_records"][0]["raw_record"][fixture.field] = (
                "Changed context"
            )
            with self.assertRaises(Failure) as caught:
                generate_candidate(runtime, "good", "ast", 0, changed)
            self.assertEqual(caught.exception.kind, "checkpoint_failure")
            self.assertEqual(len(provider.requests), 7)

    def test_local_assembly_errors_become_bounded_feedback_without_probe(self):
        for broken in ("duplicate_time", "unknown_rule_slot"):

            class BrokenAssemblyProvider(ScriptedProvider):
                def generate(self, request, call_id):
                    response = super().generate(request, call_id)
                    value = response["config"]
                    if (
                        broken == "duplicate_time"
                        and request["purpose"] == "candidate_manifest"
                    ):
                        value["time_mappings"] = [
                            {
                                "slot": "valid_from",
                                "source_fields": ["row_note"],
                                "evidence_ref": "row_note",
                            }
                        ] * 2
                    if (
                        broken == "unknown_rule_slot"
                        and request["purpose"] == "candidate_rules"
                    ):
                        value["expressions"][0]["slot"] = "mapping:9"
                    return response

            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as root:
                fixture = NativeFixture(root)
                provider = BrokenAssemblyProvider(fixture, ["good", "stop"])
                result = onboard_source(fixture.request, provider)
                decisions = [
                    r for r in provider.requests if r["purpose"] == "agent_decision"
                ]
                self.assertEqual(
                    decisions[-1]["payload"]["feedback"]["error"],
                    "candidate_assembly_invalid",
                )
                self.assertIn(
                    "new candidate_id",
                    decisions[-1]["payload"]["feedback"]["repair_hint"],
                )
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["observations"], [])
                self.assertFalse(
                    (
                        Path(fixture.request["state_dir"]) / "artifacts/probe-good.json"
                    ).exists()
                )
                if broken == "duplicate_time":
                    self.assertNotIn(
                        "candidate_rules", [r["purpose"] for r in provider.requests]
                    )

    def test_empty_manifest_skips_rule_generation_and_is_not_selectable(self):
        class EmptyFixture(NativeFixture):
            def config(self, bad=False):
                return {
                    "field_mappings": [],
                    "unmapped_fields": [
                        {"source_field": key, "reason": "No supported target"}
                        for key in (self.field, "row_note")
                    ],
                    "execution": {"kind": "ast", "transforms": []},
                }

        with tempfile.TemporaryDirectory() as root:
            fixture = EmptyFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select", "stop"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertNotIn(
                "candidate_rules", [r["purpose"] for r in provider.requests]
            )
            self.assertNotIn(
                "name_role_review", [r["purpose"] for r in provider.requests]
            )
            self.assertEqual(result["observations"], [])

    def test_nonempty_unused_control_values_fail_within_decision_limit(self):
        class InvalidControl(ScriptedProvider):
            def generate(self, request, call_id):
                if request["purpose"] == "agent_decision":
                    self.requests.append(copy.deepcopy(request))
                    return {
                        "config": control("stop", reason="Stop", backend="ast"),
                        "cost_usd": 0,
                        "usage": {},
                    }
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = InvalidControl(fixture)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["gap"]["kind"], "decision_limit")
            self.assertEqual(result["agent_decisions"], MAX_DECISIONS)
            self.assertNotIn(
                "candidate_manifest", [r["purpose"] for r in provider.requests]
            )

    def test_every_task_receives_full_docs_and_only_exposed_proposal_rows(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            result = onboard_source(fixture.request, provider)
            run = Path(fixture.request["state_dir"])
            proposal = json.loads((run / "artifacts/proposal-records.json").read_text())
            withheld = {r["source_record_id"] for r in result["observations"]}
            for request in provider.requests:
                if request["purpose"] not in {
                    "agent_decision",
                    "candidate_manifest",
                    "candidate_rules",
                }:
                    continue
                self.assertEqual(request["payload"]["proposal_records"], proposal)
                self.assertFalse({r["source_record_id"] for r in proposal} & withheld)
                self.assertIn(
                    fixture.definition, request["payload"]["source_documentation"]
                )
                self.assertEqual(
                    request["payload"]["ontology_text"], fixture.ontology.read_text()
                )


if __name__ == "__main__":
    unittest.main()
