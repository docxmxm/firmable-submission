"""Offline behavior checks over newly constructed sources, without external model calls."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.agent import (
    _compile_finding,
    _execute,
    _execution_cost_context,
    _feedback,
    development_partition,
    onboard_source,
)
from firmable_runtime.common import Failure, digest
from firmable_runtime.execution import pointer_get
from firmable_runtime.schema import check_response
from test_config_audit import scripted_branches


class NativeFixture:
    """One native input and its field-bound documentation, outside run output."""

    def __init__(self, root, field="registered_title", native_format="csv"):
        self.root = Path(root)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.field = field
        self.rows = [
            {field: name + " Pty Ltd", "row_note": "Reference " + str(index)}
            for index, name in enumerate(
                ("Aster", "Birch", "Cedar", "Dahlia", "Elm", "Fern", "Gum", "Hazel")
            )
        ]
        if native_format == "csv":
            self.native = self.evidence / "register.csv"
            with self.native.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=[field, "row_note"])
                writer.writeheader()
                writer.writerows(self.rows)
        else:
            self.native = self.evidence / "register.json"
            self.native.write_text(json.dumps({"records": self.rows}))
        self.definition = field + ": Legal name of the company."
        (self.evidence / "definitions.md").write_text(
            self.definition
            + "\nrow_note: Internal row reference.\nLicence: CC BY 4.0\n"
        )
        self.ontology = self.root / "ontology.yaml"
        self.ontology.write_text("entity:\n  legal_name: string\n")
        self.request = {
            "source_id": "unseen-register",
            "evidence_root": str(self.evidence),
            "state_dir": str(self.root / "run"),
            "ontology_path": str(self.ontology),
            "catalogue_metadata": {"licence": "CC BY 4.0"},
            "dev_record_limit": 8,
            "proposal_record_limit": 3,
        }

    def config(self, bad=False):
        return {
            "field_mappings": [
                {
                    "source_field": self.field,
                    "source_fields": [self.field],
                    "canonical_field": "entity.legal_name",
                    "confidence": 0.95,
                    "transformation": "Copy the documented company legal name.",
                    "semantic_evidence": self.definition,
                    "execution_pointer": "/execution/transforms/0",
                    "validation_pass": False,
                }
            ],
            "unmapped_fields": [
                {
                    "source_field": "row_note",
                    "reason": "Internal reference has no supported ontology target.",
                }
            ],
            "execution": {
                "kind": "ast",
                "transforms": [
                    {
                        "op": "field",
                        "name": "absent_native_field" if bad else self.field,
                    }
                ],
            },
        }


def control(action, **kwargs):
    return {
        "action": action,
        "candidate_id": "",
        "backend": "",
        "field": "",
        "reason": "",
        **kwargs,
    }


def candidate_response(request, config):
    """Split an offline fixture across the real task contracts, never infer it."""
    if request["purpose"] == "candidate_manifest":
        value = {
            "field_mappings": [],
            "unmapped_fields": copy.deepcopy(config["unmapped_fields"]),
        }
        for mapping in config["field_mappings"]:
            item = {
                key: copy.deepcopy(value)
                for key, value in mapping.items()
                if key not in {"execution_pointer", "validation_pass"}
            }
            item.setdefault("claim_kind", "informative")
            value["field_mappings"].append(item)
        if config["execution"].get("kind", "ast") == "ast":
            value["time_mappings"] = [
                {
                    "slot": slot,
                    "source_fields": spec["source_fields"],
                    "evidence_ref": spec["evidence_ref"],
                }
                for slot in ("observed_time", "valid_from", "valid_to")
                if (spec := config["execution"].get(slot)) is not None
            ]
        return value
    if request["purpose"] == "candidate_rules":
        return {
            "expressions": [
                {
                    "slot": slot,
                    "ast": copy.deepcopy(
                        pointer_get(
                            config,
                            config["field_mappings"][int(slot.split(":")[1])][
                                "execution_pointer"
                            ],
                        )
                        if slot.startswith("mapping:")
                        else config["execution"][slot.split(":")[1]]["ast"]
                    ),
                }
                for slot in request["payload"]["expression_slots"]
            ]
        }
    return {
        "tasks": [
            {
                "slot": "mapping:" + str(index),
                "instructions": config["execution"]["task"],
            }
            for index in range(len(config["field_mappings"]))
        ],
        "subject": config["execution"]["subject"],
    }


class ScriptedProvider:
    """Return a prescribed action sequence; record exactly what the planner sees."""

    model_version = "offline-scripted-model"

    def __init__(self, fixture, actions=None):
        self.fixture = fixture
        self.actions = list(actions or ["bad", "good", "select"])
        self.requests = []
        self.candidate_configs = {}

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        if request["purpose"] == "inspect":
            value = {
                "selected_table_ids": [0],
                "reason": "The native table contains company records.",
            }
        elif request["purpose"] == "name_role_review":
            value = {
                "reviews": [
                    {
                        "mapping_index": item["mapping_index"],
                        "verdict": "supported",
                        "evidence_quotes": [
                            {"kind": "documentation", "quote": self.fixture.definition}
                        ],
                        "reason": "The fixture explicitly documents the company's legal name without historical conditions.",
                        "repair_hint": "No revision needed for the fixture name mapping.",
                    }
                    for item in request["payload"]["name_mappings"]
                ]
            }
            value = scripted_branches(value, request["payload"])
        elif request["purpose"] in {
            "candidate_manifest",
            "candidate_rules",
            "candidate_task",
        }:
            value = candidate_response(
                request, self.candidate_configs[request["payload"]["candidate_id"]]
            )
        else:
            action = self.actions.pop(0)
            if isinstance(action, dict):
                value = action
            elif action in {"bad", "good", "repeat"}:
                config = self.fixture.config(bad=action != "good")
                self.candidate_configs[action] = config
                value = control(
                    "probe",
                    candidate_id=action,
                    backend=config["execution"].get("kind", "ast"),
                )
            elif action == "select":
                value = control("select", candidate_id="good")
            elif action == "select_bad":
                value = control("select", candidate_id="bad")
            else:
                value = control("stop", reason="No supported candidate was selected.")
        check_response(value, request["output_schema"])
        return {
            "config": value,
            "provider_receipt": {"call_id": call_id, "phase": "offline_fixture"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "cost_usd": 0.0,
        }


class MixedFixture(NativeFixture):
    def __init__(self, root):
        super().__init__(root, field="EntityLabel")
        for row in self.rows:
            row.update(identifier="51824753556", TradeName="Trading " + row["row_note"])
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        (self.evidence / "definitions.md").write_text(
            "EntityLabel\nTradeName\nidentifier: Australian Business Number (ABN).\n"
            "row_note: Internal reference.\nLicence: CC BY 4.0\n"
        )
        self.request["required_fields"] = [
            "entity.abn",
            "entity.legal_name",
            "entity.trading_name",
        ]

    def config(self, bad=False):
        config = {
            "field_mappings": [],
            "unmapped_fields": [
                {"source_field": "row_note", "reason": "Internal reference."}
            ],
            "execution": {"kind": "ast", "transforms": []},
        }
        for field, target in (
            ("identifier", "entity.abn"),
            (self.field, "entity.legal_name"),
            ("TradeName", "entity.trading_name"),
        ):
            if target == "entity.legal_name" and not bad:
                config["unmapped_fields"].append(
                    {
                        "source_field": field,
                        "reason": "Legal role not established.",
                    }
                )
                continue
            ast = {"op": "field", "name": field}
            if target == "entity.abn":
                ast = {
                    "op": "parse_typed_id",
                    "input": ast,
                    "kind": "abn",
                    "require_label": False,
                }
            config["field_mappings"].append(
                {
                    "source_field": field,
                    "source_fields": [field],
                    "canonical_field": target,
                    "confidence": 0.9,
                    "transformation": "Copy or validate the declared native value.",
                    "semantic_evidence": "identifier: Australian Business Number (ABN)."
                    if target == "entity.abn"
                    else field,
                    "execution_pointer": "/execution/transforms/"
                    + str(len(config["execution"]["transforms"])),
                    "validation_pass": False,
                }
            )
            config["execution"]["transforms"].append(ast)
        return config


class MixedProvider(ScriptedProvider):
    def generate(self, request, call_id):
        if request["purpose"] != "name_role_review":
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        value = scripted_branches(
            {
                "reviews": [
                    {
                        "mapping_index": mapping["mapping_index"],
                        "verdict": "uncertain"
                        if mapping["canonical_field"] == "entity.legal_name"
                        else "supported",
                        "evidence_quotes": [
                            {"kind": "header", "quote": mapping["source_field"]}
                        ],
                        "reason": "The native trading role is explicit; the generic entity label does not establish legal role.",
                        "repair_hint": "Retain the trading mapping and leave legal role unresolved.",
                    }
                    for mapping in request["payload"]["name_mappings"]
                ]
            },
            request["payload"],
        )
        check_response(value, request["output_schema"])
        return {"config": value, "cost_usd": 0, "usage": {}}


class SubsetProvider(MixedProvider):
    """A planner that stops or switches backend before trying its supported AST."""

    def generate(self, request, call_id):
        if request["purpose"] == "agent_decision" and self.actions[0] in {
            "select_subset",
            "llm",
        }:
            self.requests.append(copy.deepcopy(request))
            action = self.actions.pop(0)
            if action == "llm":
                value = control(
                    "probe", candidate_id="llm-after-role-failure", backend="llm_task"
                )
            else:
                candidate = next(
                    item
                    for item in request["payload"]["candidates"]
                    if item["candidate_id"].startswith("host-subset-")
                )
                value = control("select", candidate_id=candidate["candidate_id"])
            check_response(value, request["output_schema"])
            return {"config": value, "cost_usd": 0, "usage": {}}
        return super().generate(request, call_id)


class AgentTests(unittest.TestCase):
    def test_development_failure_survives_invalid_control_and_reselection(self):
        """Exercise the real journal/planner with one scripted development fault."""
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            fixture.request["required_fields"] = ["entity.legal_name"]
            provider = ScriptedProvider(
                fixture,
                [
                    "good",
                    "select",
                    control("probe", candidate_id="revision", backend="ast"),
                    "select",
                    "stop",
                ],
            )
            validation_calls = []

            def fail_development(runtime, config, rows, *, name_review=None):
                observations, report = _execute(
                    runtime, config, rows, name_review=name_review
                )
                if rows == runtime.run.load_artifact("validation-records"):
                    validation_calls.append(True)
                    report["errors"].append(
                        {
                            "canonical_field": "entity.legal_name",
                            "reason_code": "quote_not_found",
                            "message": "quote_not_found",
                            "validation_stage": "llm_review_evidence",
                            "offending_ref": {
                                "kind": "header",
                                "field": fixture.field,
                                "quote": "withheld-original-quote",
                            },
                            "candidate_value": "withheld-candidate-value",
                        }
                    )
                    report["validation_pass"] = False
                return observations, report

            from firmable_runtime.agent import validate_decision as actual_validate

            def reject_control(action):
                if action["candidate_id"] == "revision":
                    raise Failure(
                        "validation_error",
                        "Simulated invalid control after transport validation",
                    )
                return actual_validate(action)

            with (
                patch("firmable_runtime.agent._execute", side_effect=fail_development),
                patch(
                    "firmable_runtime.agent.validate_decision",
                    side_effect=reject_control,
                ),
            ):
                result = onboard_source(fixture.request, provider)
            decisions = [
                r["payload"]
                for r in provider.requests
                if r["purpose"] == "agent_decision"
            ]
            self.assertTrue(decisions[1]["candidates"][0]["eligible"])
            self.assertEqual(
                decisions[3]["feedback"]["error"], "invalid_control_action"
            )
            for context in decisions[2:]:
                candidate = next(
                    c for c in context["candidates"] if c["candidate_id"] == "good"
                )
                self.assertFalse(candidate["eligible"])
                self.assertTrue(candidate["probe_passed"])
                self.assertEqual(candidate["validation_status"], "failed")
                self.assertEqual(candidate["feedback_scope"], "development_validation")
                error = candidate["feedback"]["errors"][0]
                self.assertEqual(error["reason_code"], "quote_not_found")
                self.assertEqual(error["validation_stage"], "llm_review_evidence")
                self.assertEqual(
                    error["reference_context"],
                    {"kind": "header", "field": fixture.field},
                )
                history = context["unresolved_requirement_history"]["entries"]
                self.assertEqual(history[0]["scope"], "development_validation")
                self.assertEqual(
                    history[0]["execution_ref"], candidate["validation_ref"]
                )
                serialized = json.dumps(context)
                self.assertNotIn("withheld-original-quote", serialized)
                self.assertNotIn("withheld-candidate-value", serialized)
            self.assertEqual(
                decisions[4]["feedback"]["error"],
                "selection_requires_revision_after_development_failure",
            )
            self.assertEqual(len(validation_calls), 1)
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertEqual(
                result["unresolved_requirement_history"]["entries"][0]["status"],
                "failed",
            )
            checkpoint = json.loads(Path(result["checkpoint_path"]).read_text())
            candidate = checkpoint["agent"]["candidates"]["good"]
            self.assertEqual(candidate["feedback"]["error_count"], 0)
            self.assertEqual(candidate["validation_feedback"]["error_count"], 1)
            calls = len(provider.requests)
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), calls)

    def test_feedback_keeps_reference_types_without_withheld_values(self):
        errors = [
            {
                "reason_code": "quote_not_found",
                "validation_stage": stage,
                "offending_ref": {
                    "kind": kind,
                    "field": "native_label",
                    "quote": "private",
                },
                "candidate_value": "private",
                "raw_refs": [{"quote": "private"}],
                "model_review": {"reason": "private"},
            }
            for stage, kind in (
                ("llm_extraction_evidence", "raw"),
                ("llm_review_evidence", "header"),
            )
        ]
        feedback = _feedback({"errors": errors}, [], {"field_mappings": []})
        self.assertEqual(
            [error["validation_stage"] for error in feedback["errors"]],
            [error["validation_stage"] for error in errors],
        )
        self.assertEqual(
            [error["reference_context"]["kind"] for error in feedback["errors"]],
            ["raw", "header"],
        )
        self.assertNotIn("private", json.dumps(feedback))
        errors[0]["offending_ref"] = {"kind": "private", "field": "private"}
        feedback = _feedback({"errors": errors}, [], {"field_mappings": []})
        self.assertEqual(feedback["errors"][0]["reference_context"], {})
        self.assertNotIn("private", json.dumps(feedback))

    def test_stop_or_semantic_backend_switch_probes_subset_before_selection(self):
        for trigger in ("stop", "llm"):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as root:
                fixture = MixedFixture(root)
                provider = SubsetProvider(fixture, ["bad", trigger, "select_subset"])
                result = onboard_source(fixture.request, provider)
                artifacts = Path(fixture.request["state_dir"]) / "artifacts"
                plan = json.loads(
                    (artifacts / "host-subset-repair-plan.json").read_text()
                )
                self.assertEqual(plan["excluded_mapping_indices"], [1])
                self.assertEqual(plan["retained_mapping_indices"], [0, 2])
                self.assertEqual(plan["source_candidate_id"], "bad")
                self.assertEqual(plan["attempt_limit"], 1)
                self.assertEqual(
                    plan["trigger"],
                    "stop" if trigger == "stop" else "semantic_backend_switch",
                )
                original = json.loads((artifacts / "candidate-bad.json").read_text())
                subset = json.loads(
                    (
                        artifacts / ("candidate-" + plan["candidate_id"] + ".json")
                    ).read_text()
                )
                self.assertEqual(digest(original), plan["source_config_hash"])
                self.assertEqual(digest(subset), plan["subset_config_hash"])
                for before, after in zip([0, 2], [0, 1]):
                    expected = copy.deepcopy(original["field_mappings"][before])
                    expected["execution_pointer"] = "/execution/transforms/" + str(
                        after
                    )
                    self.assertEqual(subset["field_mappings"][after], expected)
                    self.assertEqual(
                        subset["execution"]["transforms"][after],
                        original["execution"]["transforms"][before],
                    )
                audit = json.loads(
                    (
                        artifacts / ("name-audit-" + plan["candidate_id"] + ".json")
                    ).read_text()
                )
                self.assertEqual(
                    [item["verdict"] for item in audit["reviews"]], ["supported"]
                )
                review_calls = [
                    request
                    for request in provider.requests
                    if request["purpose"] == "name_role_review"
                ]
                self.assertTrue(
                    any(
                        len(request["payload"]["name_mappings"]) == 1
                        for request in review_calls
                    )
                )
                for purpose in ("candidate_manifest", "candidate_rules"):
                    self.assertEqual(
                        sum(
                            request["purpose"] == purpose
                            for request in provider.requests
                        ),
                        2 if purpose == "candidate_manifest" else 1,
                    )
                self.assertFalse(
                    any(
                        request["purpose"] == "candidate_task"
                        for request in provider.requests
                    )
                )
                self.assertEqual(result["candidate_status"], "validated")
                self.assertEqual(result["task_status"], "partial")
                self.assertEqual(
                    result["unresolved_requirements"], ["entity.legal_name"]
                )
                self.assertEqual(
                    result["task_requirements"]["required_fields"],
                    fixture.request["required_fields"],
                )
                self.assertTrue(
                    all(
                        row["entity"].get("trading_name")
                        for row in result["observations"]
                    )
                )
                self.assertIsNone(result["approval"])
                self.assertTrue(result["approval_required"])
                calls = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)

    def test_subset_is_one_attempt_and_never_automatically_selected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = MixedFixture(root)
            provider = SubsetProvider(fixture, ["bad", "stop", "stop", "stop"])
            result = onboard_source(fixture.request, provider)
            checkpoint = json.loads(Path(result["checkpoint_path"]).read_text())
            self.assertEqual(
                set(checkpoint["agent"]["candidates"]), {"bad", "host-subset-1"}
            )
            self.assertTrue(
                checkpoint["agent"]["candidates"]["host-subset-1"]["eligible"]
            )
            self.assertTrue(checkpoint["agent"]["subset_repair_attempted"])
            self.assertNotIn("pending_subset_repair", checkpoint["agent"])
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertIsNone(result["mapping_config"])
            self.assertIsNone(result["approval"])
            self.assertEqual(result["gap"]["kind"], "agent_stop")

    def test_stop_does_not_repeat_an_already_probed_equivalent_subset(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = MixedFixture(root)
            provider = SubsetProvider(fixture, ["bad", "good", "stop", "stop"])
            result = onboard_source(fixture.request, provider)
            checkpoint = json.loads(Path(result["checkpoint_path"]).read_text())
            self.assertFalse(checkpoint["agent"].get("subset_repair_attempted", False))
            self.assertEqual(set(checkpoint["agent"]["candidates"]), {"bad", "good"})
            self.assertEqual(result["candidate_status"], "unavailable")

    def test_fresh_subset_name_rejection_prevents_selection(self):
        class RejectedSubsetReview(SubsetProvider):
            def generate(self, request, call_id):
                response = super().generate(request, call_id)
                if (
                    request["purpose"] == "name_role_review"
                    and len(request["payload"]["name_mappings"]) == 1
                ):
                    for review in response["config"]["reviews"]:
                        review["verdict"] = "uncertain"
                        review["reason"] = (
                            "The independent fresh review cannot confirm this role."
                        )
                return response

        with tempfile.TemporaryDirectory() as root:
            fixture = MixedFixture(root)
            provider = RejectedSubsetReview(
                fixture, ["bad", "stop", "select_subset", "stop"]
            )
            result = onboard_source(fixture.request, provider)
            checkpoint = json.loads(Path(result["checkpoint_path"]).read_text())
            self.assertFalse(
                checkpoint["agent"]["candidates"]["host-subset-1"]["eligible"]
            )
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertIsNone(result["mapping_config"])
            self.assertEqual(
                provider.requests[-1]["payload"]["feedback"]["error"],
                "selection_requires_successful_informative_probe",
            )

    def test_backend_switch_for_non_semantic_compile_errors_is_not_intercepted(self):
        class BrokenExpressionFixture(MixedFixture):
            def config(self, bad=False):
                config = super().config(bad=False)
                if bad:
                    config["execution"]["transforms"][0]["input"]["name"] = (
                        "absent_native_field"
                    )
                return config

        class CaptureBackendRequest(SubsetProvider):
            def generate(self, request, call_id):
                if (
                    request["purpose"] == "candidate_manifest"
                    and request["payload"]["backend"] == "llm_task"
                ):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError(
                        "Offline boundary: requested LLM backend reached assembly."
                    )
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = BrokenExpressionFixture(root)
            provider = CaptureBackendRequest(fixture, ["bad", "llm"])
            with self.assertRaises(Failure) as caught:
                onboard_source(fixture.request, provider)
            self.assertEqual(caught.exception.kind, "model_outcome_unknown")
            self.assertEqual(provider.requests[-1]["purpose"], "candidate_manifest")
            self.assertEqual(provider.requests[-1]["payload"]["backend"], "llm_task")
            checkpoint = json.loads(
                (
                    Path(fixture.request["state_dir"]) / "checkpoint.internal.json"
                ).read_text()
            )
            self.assertFalse(checkpoint["agent"].get("subset_repair_attempted", False))
            self.assertNotIn("host-subset-repair-plan", checkpoint["artifacts"])

    def test_subset_fresh_review_unknown_is_never_resent(self):
        class UnknownSubsetReview(SubsetProvider):
            def generate(self, request, call_id):
                if (
                    request["purpose"] == "name_role_review"
                    and len(request["payload"]["name_mappings"]) == 1
                ):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError(
                        "Fresh subset review may have completed externally."
                    )
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = MixedFixture(root)
            provider = UnknownSubsetReview(fixture, ["bad", "stop"])
            calls = None
            for _ in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                if calls is not None:
                    self.assertEqual(len(provider.requests), calls)
                calls = len(provider.requests)
            checkpoint = json.loads(
                (
                    Path(fixture.request["state_dir"]) / "checkpoint.internal.json"
                ).read_text()
            )
            self.assertEqual(
                checkpoint["agent"]["pending_subset_repair"], "host-subset-repair-plan"
            )
            self.assertTrue(checkpoint["agent"]["subset_repair_attempted"])
            self.assertNotIn("host-subset-1", checkpoint["agent"]["candidates"])
            self.assertNotIn("final-config", checkpoint["artifacts"])

    def test_compile_identity_comes_only_from_a_valid_config_index(self):
        config = {
            "field_mappings": [
                {"canonical_field": "entity.legal_name", "source_fields": ["label"]}
            ]
        }
        finding = {
            "code": "semantic_rule",
            "message": "entity.trading_name failed",
            "pointer": "/field_mappings/0",
            "canonical_field": "entity.trading_name",
            "source_fields": ["unbound"],
            "semantic_support": {"supported": False},
        }
        bound = _compile_finding({**finding, "mapping_index": 0}, config, "failure")
        self.assertEqual(bound["canonical_field"], "entity.legal_name")
        self.assertEqual(bound["source_fields"], ["label"])
        self.assertEqual(bound["semantic_support"], finding["semantic_support"])
        for index in (None, True, -1, 1, "0"):
            with self.subTest(index=index):
                error = _compile_finding(
                    {**finding, "mapping_index": index}, config, "failure"
                )
                self.assertEqual(error["error_scope"], "candidate")
                self.assertNotIn("canonical_field", error)
                self.assertNotIn("mapping_index", error)

    def test_real_legal_compile_failure_retains_trade_for_partial_revision(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = MixedFixture(root)
            provider = MixedProvider(fixture)
            result = onboard_source(fixture.request, provider)
            decisions = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ]
            feedback = decisions[1]["payload"]["feedback"]
            compile_error = next(
                e for e in feedback["errors"] if e["reason_code"] == "semantic_rule"
            )
            self.assertEqual(compile_error["mapping_index"], 1)
            self.assertEqual(compile_error["canonical_field"], "entity.legal_name")
            self.assertEqual(compile_error["source_fields"], [fixture.field])
            self.assertFalse(compile_error["semantic_support"]["supported"])
            self.assertEqual(feedback["execution_status"], "not_executed")
            self.assertEqual(feedback["failure_stage"], "compilation")
            statuses = {
                item["canonical_field"]: item
                for item in feedback["task_acceptance"]["fields"]
            }
            self.assertEqual(statuses["entity.legal_name"]["status"], "failed")
            for target in ("entity.abn", "entity.trading_name"):
                self.assertEqual(statuses[target]["status"], "needs_evidence")
                self.assertEqual(
                    statuses[target]["reason_code"], "candidate_not_executed"
                )
                self.assertEqual(statuses[target]["diagnostics"], [])
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(result["task_status"], "partial")
            self.assertEqual(result["unresolved_requirements"], ["entity.legal_name"])
            self.assertTrue(
                all(row["entity"].get("trading_name") for row in result["observations"])
            )
            self.assertTrue(
                all(
                    not row["entity"].get("legal_name")
                    for row in result["observations"]
                )
            )
            self.assertEqual(
                result["task_requirements"]["required_fields"],
                fixture.request["required_fields"],
            )

    def test_required_name_cannot_disappear_after_failed_candidate(self):
        class IdentifierFixture(NativeFixture):
            def config(self, bad=False):
                config = super().config(bad=bad)
                identifier = {
                    "source_field": "identifier",
                    "source_fields": ["identifier"],
                    "canonical_field": "entity.abn",
                    "confidence": 0.95,
                    "transformation": "Validate the documented ABN.",
                    "semantic_evidence": "identifier: Australian Business Number (ABN).",
                    "execution_pointer": "/execution/transforms/1"
                    if bad
                    else "/execution/transforms/0",
                    "validation_pass": False,
                }
                expression = {
                    "op": "parse_typed_id",
                    "input": {"op": "field", "name": "identifier"},
                    "kind": "abn",
                    "require_label": False,
                }
                if bad:
                    config["field_mappings"].append(identifier)
                    config["execution"]["transforms"].append(expression)
                else:
                    config["field_mappings"] = [identifier]
                    config["execution"]["transforms"] = [expression]
                    config["unmapped_fields"].append(
                        {
                            "source_field": self.field,
                            "reason": "Cannot establish a supported name mapping.",
                        }
                    )
                return config

        with tempfile.TemporaryDirectory() as root:
            fixture = IdentifierFixture(root)
            for row in fixture.rows:
                row["identifier"] = "51824753556"
            with fixture.native.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=[fixture.field, "row_note", "identifier"]
                )
                writer.writeheader()
                writer.writerows(fixture.rows)
            definitions = fixture.evidence / "definitions.md"
            definitions.write_text(
                definitions.read_text()
                + "identifier: Australian Business Number (ABN).\n"
            )
            fixture.request["required_fields"] = ["entity.abn", "entity.legal_name"]
            provider = ScriptedProvider(fixture)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(result["task_status"], "partial")
            self.assertEqual(result["unresolved_requirements"], ["entity.legal_name"])
            self.assertIsNotNone(result["mapping_config"])
            by_field = {
                item["canonical_field"]: item
                for item in result["task_acceptance"]["fields"]
            }
            self.assertEqual(by_field["entity.abn"]["status"], "complete")
            self.assertEqual(by_field["entity.legal_name"]["status"], "needs_evidence")
            decisions = [
                item
                for item in provider.requests
                if item["purpose"] == "agent_decision"
            ]
            for item in decisions:
                self.assertEqual(
                    item["payload"]["task_requirements"]["required_fields"],
                    ["entity.abn", "entity.legal_name"],
                )
            self.assertTrue(
                any(
                    item["candidate_id"] == "bad"
                    and item["canonical_field"] == "entity.legal_name"
                    for item in decisions[-1]["payload"][
                        "unresolved_requirement_history"
                    ]["entries"]
                )
            )
            calls = len(provider.requests)
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), calls)
            fixture.request["required_fields"] = ["entity.abn"]
            with self.assertRaises(Failure) as error:
                onboard_source(fixture.request, provider)
            self.assertEqual(error.exception.kind, "checkpoint_mismatch")
            self.assertEqual(len(provider.requests), calls)

    def test_declared_supported_field_passes_without_changing_legacy_task_scope(self):
        for required in ([], ["entity.legal_name"]):
            with self.subTest(required=required), tempfile.TemporaryDirectory() as root:
                fixture = NativeFixture(root)
                fixture.request["required_fields"] = required
                result = onboard_source(
                    fixture.request, ScriptedProvider(fixture, ["good", "select"])
                )
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["candidate_status"], "validated")
                self.assertEqual(
                    result["task_status"], "complete" if required else "not_configured"
                )

    def test_native_execution_failure_is_revised_and_validation_stays_withheld(self):
        for field, native_format in (
            ("registered_title", "csv"),
            ("entity_label", "json"),
        ):
            with (
                self.subTest(native_format=native_format),
                tempfile.TemporaryDirectory() as root,
            ):
                fixture = NativeFixture(root, field, native_format)
                provider = ScriptedProvider(fixture)
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["agent_decisions"], 3)
                self.assertEqual(result["usage"]["call_count"], 10)
                self.assertTrue(result["approval_required"])
                self.assertIsNone(result["approval"])
                decisions = [
                    request
                    for request in provider.requests
                    if request["purpose"] == "agent_decision"
                ]
                failure = decisions[1]["payload"]["feedback"]
                self.assertGreater(failure["error_count"], 0)
                self.assertEqual(
                    failure["output_summary"]["informative_field_value_count"], 0
                )
                proposal_ids = {
                    row["source_record_id"]
                    for row in decisions[0]["payload"]["proposal_records"]
                }
                validation_ids = {
                    row["source_record_id"] for row in result["observations"]
                }
                self.assertTrue(proposal_ids)
                self.assertTrue(validation_ids)
                self.assertFalse(proposal_ids & validation_ids)
                cost_context = decisions[0]["payload"]["execution_cost_context"]
                self.assertEqual(cost_context["selected_native_record_count"], 8)
                self.assertEqual(cost_context["discovered_native_record_count"], 8)
                self.assertEqual(cost_context["ast"]["full_extraction_model_calls"], 0)
                self.assertEqual(cost_context["llm_task"]["records_per_batch"], 4)
                self.assertEqual(
                    cost_context["llm_task"][
                        "full_extraction_calls_if_each_batch_has_claims"
                    ],
                    4,
                )
                for decision in decisions:
                    self.assertEqual(
                        {
                            row["source_record_id"]
                            for row in decision["payload"]["proposal_records"]
                        },
                        proposal_ids,
                    )
                self.assertEqual(
                    {row["entity"]["legal_name"] for row in result["observations"]},
                    {row["raw_record"][field] for row in result["observations"]},
                )
                calls_before = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls_before)

    def test_failed_candidate_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["bad", "select_bad", "stop"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["mapping_config"])
            self.assertEqual(
                provider.requests[-1]["payload"]["feedback"]["error"],
                "selection_requires_successful_informative_probe",
            )

    def test_cost_context_counts_full_selected_tables_and_rounds_partial_batches(self):
        from types import SimpleNamespace

        runtime = SimpleNamespace(
            tables=[
                {"records": [None] * 1001},
                {"records": [None] * 20},
                {"status": "unsupported"},
            ],
            run=SimpleNamespace(state={"selected_tables": [0]}),
        )
        context = _execution_cost_context(runtime)
        self.assertEqual(context["discovered_native_record_count"], 1021)
        self.assertEqual(context["selected_native_record_count"], 1001)
        self.assertEqual(context["llm_task"]["full_extraction_batch_count"], 251)
        self.assertEqual(
            context["llm_task"]["full_extraction_calls_if_each_batch_has_claims"], 502
        )

    def test_unchanged_failed_candidate_stops_instead_of_repeating(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["bad", "repeat"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["gap"]["kind"], "no_progress")
            self.assertEqual(result["agent_decisions"], 2)

    def test_duplicate_native_content_never_crosses_proposal_validation_boundary(self):
        records = [
            {
                "source_id": "source",
                "source_record_id": str(index),
                "raw_record": {"value": index // 2},
            }
            for index in range(8)
        ]
        proposal, validation = development_partition(records, limit=8, proposal_limit=3)
        self.assertFalse(
            {row["raw_record"]["value"] for row in proposal}
            & {row["raw_record"]["value"] for row in validation}
        )
        self.assertEqual(len(proposal) + len(validation), 8)

    def test_unknown_provider_outcome_is_durable_and_never_resent(self):
        class TimeoutProvider:
            model_version = "offline-timeout-model"
            calls = 0

            def generate(self, request, call_id):
                self.calls += 1
                raise TimeoutError("The request might have been processed.")

        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = TimeoutProvider()
            for _ in range(2):
                with self.assertRaises(Failure) as failure:
                    onboard_source(fixture.request, provider)
                self.assertEqual(failure.exception.kind, "model_outcome_unknown")
            self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
