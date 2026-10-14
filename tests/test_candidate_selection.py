"""Candidate comparison measures development output without selecting a winner."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.agent import _candidate_execution, _execute, onboard_source
from firmable_runtime.common import Failure
from firmable_runtime.runtime import Runtime
from test_agent import NativeFixture, ScriptedProvider, control


class SelectionFixture(NativeFixture):
    def __init__(self, root, renamed=False):
        super().__init__(root)
        self.id_field, self.kind_field, self.address_field = (
            ("Key Q", "Class R", "Place S")
            if renamed
            else ("identifier", "business_kind", "location_value")
        )
        self.rows = [
            {
                self.id_field: "51824753556",
                self.kind_field: "Company",
                self.address_field: str(index + 1) + " Example St, Richmond VIC 3121",
                "other_address": str(index + 10) + " Elsewhere Rd, Richmond VIC 3121",
            }
            for index in range(8)
        ]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        self.definitions = {
            self.id_field: self.id_field + ": Australian Business Number (ABN).",
            self.kind_field: self.kind_field + ": Legal entity type.",
            self.address_field: self.address_field
            + ": Postal address of the business.",
            "other_address": "other_address: Postal address of the business.",
        }
        (self.evidence / "definitions.md").write_text(
            "\n".join(self.definitions.values())
        )
        self.request["required_fields"] = ["entity.abn"]

    def named_config(self, name):
        specs = [
            (
                self.id_field,
                "entity.abn",
                {
                    "op": "parse_typed_id",
                    "input": {"op": "field", "name": self.id_field},
                    "kind": "abn",
                    "require_label": False,
                },
            )
        ]
        if name != "narrow":
            specs.append(
                (
                    self.kind_field,
                    "entity.entity_type",
                    {
                        "op": "enum_lookup",
                        "input": {"op": "field", "name": self.kind_field},
                        "values": {"Company": "company"},
                        "default": None,
                    },
                )
            )
            field = "other_address" if name == "different" else self.address_field
            specs.append(
                (
                    field,
                    "address.full",
                    {"op": "trim", "input": {"op": "field", "name": field}},
                )
            )
        config = {
            "field_mappings": [],
            "unmapped_fields": [],
            "execution": {"kind": "ast", "transforms": []},
        }
        for index, (field, target, ast) in enumerate(specs):
            config["field_mappings"].append(
                {
                    "source_field": field,
                    "source_fields": [field],
                    "canonical_field": target,
                    "confidence": 0.9,
                    "transformation": "Copy or validate the documented business field.",
                    "semantic_evidence": self.definitions[field],
                    "execution_pointer": "/execution/transforms/" + str(index),
                    "validation_pass": False,
                }
            )
            config["execution"]["transforms"].append(ast)
        used = {field for field, _, _ in specs}
        config["unmapped_fields"] = [
            {"source_field": field, "reason": "Not included in this candidate."}
            for field in self.rows[0]
            if field not in used
        ]
        return config


class SelectionProvider(ScriptedProvider):
    def generate(self, request, call_id):
        if (
            request["purpose"] == "agent_decision"
            and self.actions
            and isinstance(self.actions[0], str)
            and self.actions[0]
            in {
                "wide",
                "narrow",
                "different",
            }
        ):
            name = self.actions.pop(0)
            self.candidate_configs[name] = self.fixture.named_config(name)
            self.actions.insert(0, control("probe", candidate_id=name, backend="ast"))
        return super().generate(request, call_id)


class TradeoffFixture(SelectionFixture):
    """Equal observed IDs, but different rules on unsampled entity kinds."""

    def named_config(self, name):
        config = super().named_config(name)
        if name == "narrow":
            config["execution"]["transforms"][0] = {
                "op": "when",
                "condition": {"op": "equals", "input": {"op": "field", "name": self.kind_field}, "value": "Company"},
                "then": config["execution"]["transforms"][0],
                "else": {"op": "omit", "reason": "Other kinds remain unresolved."},
            }
            config["field_mappings"][0]["source_fields"].append(self.kind_field)
            config["unmapped_fields"] = [m for m in config["unmapped_fields"] if m["source_field"] != self.kind_field]
        return config


def select(name):
    return control("select", candidate_id=name)


class CandidateSelectionTests(unittest.TestCase):
    def test_unsampled_extra_enum_target_remains_a_gap_without_selecting_peer(self):
        class SparseFixture(SelectionFixture):
            def named_config(self, name):
                config = super().named_config(name)
                if name != "narrow":
                    # All proposal/development rows say Company. The peer has
                    # a legal, explicitly named branch for another entity form
                    # that neither exposed sample happens to exercise.
                    config["field_mappings"] = config["field_mappings"][:2]
                    config["execution"]["transforms"] = config["execution"][
                        "transforms"
                    ][:2]
                    config["execution"]["transforms"][1]["values"] = {
                        "Incorporated Association": "incorporated_association"
                    }
                    config["unmapped_fields"].append(
                        {
                            "source_field": self.address_field,
                            "reason": "Not included in this candidate.",
                        }
                    )
                return config

        with tempfile.TemporaryDirectory() as root:
            fixture = SparseFixture(root, renamed=True)
            provider = SelectionProvider(
                fixture, ["wide", "narrow", select("narrow"), select("narrow")]
            )
            result = onboard_source(fixture.request, provider)
            decision = [
                request["payload"]
                for request in provider.requests
                if request["purpose"] == "agent_decision"
            ][-1]
            self.assertEqual(
                decision["feedback"]["error"],
                "candidate_comparison_requires_explicit_selection",
            )
            ledger = result["candidate_selection"]
            peer = ledger["alternatives"][0]
            self.assertEqual(peer["validation_status"], "passed")
            self.assertEqual(peer["extra_targets"], ["entity.entity_type"])
            self.assertEqual(peer["additional_value_counts"], {})
            self.assertEqual(peer["shared_value_disagreement_counts"], {})
            self.assertTrue(peer["unresolved"])
            self.assertTrue(ledger["unresolved"])
            self.assertEqual(ledger["selected_candidate_id"], "narrow")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["gap"]["kind"], "candidate_selection_unresolved")
            self.assertEqual(result["task_acceptance"]["status"], "complete")
            self.assertIsNone(result["approval"])
            runtime = Runtime(fixture.request, provider)
            candidate = runtime.run.state["agent"]["candidates"]["wide"]
            for name, development in (
                ("proposal-records", False), ("validation-records", True)
            ):
                rows = runtime.run.load_artifact(name)
                self.assertTrue(all(
                    row["raw_record"][fixture.kind_field] == "Company"
                    for row in rows
                ))
                observed = _candidate_execution(
                    runtime, candidate, rows, development=development
                )
                self.assertTrue(all(
                    row.get("entity", {}).get("entity_type") in (None, "unknown")
                    for row in observed
                ))

    def test_execution_binding_rejects_records_config_and_review_changes(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(
                fixture, ["wide", "narrow", select("narrow"), select("wide")]
            )
            onboard_source(fixture.request, provider)
            runtime = Runtime(fixture.request, provider)
            candidate = runtime.run.state["agent"]["candidates"]["wide"]
            records = runtime.run.load_artifact("validation-records")
            for change in ("records", "config", "review"):
                altered = copy.deepcopy(candidate)
                if change == "config":
                    altered["config_hash"] = "different"
                if change == "review":
                    altered["name_review_artifact"] = "name-audit-wide"
                with self.subTest(change=change), self.assertRaises(Failure) as caught:
                    _candidate_execution(
                        runtime,
                        altered,
                        records[:-1] if change == "records" else records,
                        development=True,
                    )
                self.assertEqual(caught.exception.kind, "checkpoint_mismatch")

    def test_llm_alternative_never_gets_automatic_development_execution(self):
        class LLMFixture(SelectionFixture):
            def named_config(self, name):
                config = super().named_config(name)
                if name == "wide":
                    config["execution"] = {
                        "kind": "llm_task",
                        "task": "Read the bound business fields.",
                        "subject": "the business",
                    }
                    for mapping in config["field_mappings"]:
                        mapping["execution_pointer"] = "/execution/task"
                return config

        class LLMProvider(SelectionProvider):
            def generate(self, request, call_id):
                response = super().generate(request, call_id)
                if (
                    request["purpose"] == "agent_decision"
                    and response["config"]["candidate_id"] == "wide"
                    and response["config"]["action"] == "probe"
                ):
                    response["config"]["backend"] = "llm_task"
                return response

        with tempfile.TemporaryDirectory() as root:
            fixture = LLMFixture(root)
            provider = LLMProvider(
                fixture, ["wide", "narrow", select("narrow"), select("narrow")]
            )
            llm_scopes = []

            def execute(runtime, config, rows, *, name_review=None):
                if config["execution"]["kind"] == "llm_task":
                    llm_scopes.append(rows == runtime.proposal_records())
                    ast_config = SelectionFixture.named_config(fixture, "wide")
                    ast_config["source_schema"] = config["source_schema"]
                    config = ast_config
                return _execute(runtime, config, rows, name_review=name_review)

            with patch("firmable_runtime.agent._execute", side_effect=execute):
                result = onboard_source(fixture.request, provider)
            self.assertEqual(llm_scopes, [True])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(
                result["candidate_selection"]["alternatives"][0]["validation_status"],
                "not_run",
            )

    def test_comparison_requires_explicit_choice_and_retains_nonidentical_rule_loss(self):
        for choice, renamed in (("narrow", False), ("wide", True)):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as root:
                fixture = TradeoffFixture(root, renamed)
                provider = SelectionProvider(
                    fixture, ["wide", "narrow", select("narrow"), select(choice)]
                )
                result = onboard_source(fixture.request, provider)
                decision = [
                    r["payload"]
                    for r in provider.requests
                    if r["purpose"] == "agent_decision"
                ][-1]
                self.assertEqual(
                    decision["feedback"]["error"],
                    "candidate_comparison_requires_explicit_selection",
                )
                comparison = decision["feedback"]["comparison"]
                self.assertEqual(
                    comparison["alternatives"][0]["additional_value_counts"],
                    {
                        "address.full": 5,
                        "entity.entity_type": 5,
                    },
                )
                self.assertEqual(
                    result["candidate_selection"]["selected_candidate_id"], choice
                )
                self.assertEqual(
                    result["status"], "partial" if choice == "narrow" else "complete"
                )
                self.assertEqual(result["task_acceptance"]["status"], "complete")
                self.assertIsNone(result["approval"])
                self.assertNotIn("Example St", json.dumps(decision["feedback"]))
                calls = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)

    def test_shared_value_disagreement_is_a_tradeoff_not_an_automatic_winner(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(
                fixture, ["wide", "different", select("wide"), select("wide")]
            )
            result = onboard_source(fixture.request, provider)
            alternative = result["candidate_selection"]["alternatives"][0]
            self.assertEqual(alternative["additional_value_counts"], {})
            self.assertEqual(
                alternative["shared_value_disagreement_counts"], {"address.full": 5}
            )
            self.assertEqual(
                result["candidate_selection"]["selected_candidate_id"], "wide"
            )
            self.assertEqual(result["status"], "partial")

    def test_failed_development_peer_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(
                fixture,
                ["wide", "narrow", select("narrow"), select("wide"), select("narrow")],
            )

            def fail_wide(runtime, config, rows, *, name_review=None):
                observations, report = _execute(
                    runtime, config, rows, name_review=name_review
                )
                if len(
                    config["field_mappings"]
                ) > 1 and rows == runtime.run.load_artifact("validation-records"):
                    report["errors"].append(
                        {"reason_code": "fixture_development_failure"}
                    )
                return observations, report

            with patch("firmable_runtime.agent._execute", side_effect=fail_wide):
                result = onboard_source(fixture.request, provider)
            contexts = [
                r["payload"]
                for r in provider.requests
                if r["purpose"] == "agent_decision"
            ]
            self.assertEqual(
                contexts[-1]["feedback"]["error"],
                "selection_requires_revision_after_development_failure",
            )
            self.assertEqual(
                result["candidate_selection"]["selected_candidate_id"], "narrow"
            )
            self.assertEqual(result["status"], "complete")

    def test_partial_stop_reminder_never_selects(self):
        for last in (
            select("narrow"),
            control("stop", reason="Unresolved name evidence."),
        ):
            with self.subTest(last=last), tempfile.TemporaryDirectory() as root:
                fixture = SelectionFixture(root)
                fixture.request["required_fields"].append("entity.legal_name")
                provider = SelectionProvider(fixture, ["narrow", "stop", last])
                result = onboard_source(fixture.request, provider)
                context = [
                    r["payload"]
                    for r in provider.requests
                    if r["purpose"] == "agent_decision"
                ][-1]
                self.assertEqual(
                    context["feedback"]["error"],
                    "selection_required_after_successful_probe",
                )
                self.assertEqual(result["status"], "partial")
                self.assertEqual(
                    result["candidate_status"],
                    "validated" if last["action"] == "select" else "unavailable",
                )
                self.assertIsNone(result["approval"])

    def test_last_decision_preserves_unvalidated_alternative_gap(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(fixture, ["wide", "narrow", select("narrow")])
            with patch("firmable_runtime.agent.MAX_DECISIONS", 3):
                result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["gap"]["kind"], "candidate_selection_unresolved")
            alternative = result["candidate_selection"]["alternatives"][0]
            self.assertEqual(alternative["validation_status"], "not_run")
            self.assertEqual(alternative["scope"], "proposal_only")

    def test_interrupted_comparison_reuses_each_development_execution(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(
                fixture, ["wide", "narrow", select("narrow"), select("wide")]
            )
            executions = []
            original_event = Runtime.event

            def execute(runtime, config, rows, *, name_review=None):
                if rows == runtime.run.load_artifact("validation-records"):
                    executions.append(len(config["field_mappings"]))
                return _execute(runtime, config, rows, name_review=name_review)

            def interrupt(runtime, step, *args, **kwargs):
                if step == "validation" and len(executions) == 2:
                    raise RuntimeError("Crash after saved peer execution")
                return original_event(runtime, step, *args, **kwargs)

            with patch("firmable_runtime.agent._execute", side_effect=execute):
                with patch.object(
                    Runtime, "event", side_effect=interrupt, autospec=True
                ):
                    with self.assertRaisesRegex(RuntimeError, "saved peer"):
                        onboard_source(fixture.request, provider)
                result = onboard_source(fixture.request, provider)
            self.assertEqual(sorted(executions), [1, 3])
            self.assertEqual(
                result["candidate_selection"]["selected_candidate_id"], "wide"
            )

    def test_unknown_after_comparison_never_resends_or_selects(self):
        class UnknownProvider(SelectionProvider):
            def generate(self, request, call_id):
                if (
                    request["purpose"] == "agent_decision"
                    and (request["payload"].get("feedback") or {}).get("error")
                    == "candidate_comparison_requires_explicit_selection"
                ):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Unknown decision outcome")
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = UnknownProvider(fixture, ["wide", "narrow", select("narrow")])
            calls = None
            for _ in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                calls = len(provider.requests) if _ == 0 else calls
                self.assertEqual(len(provider.requests), calls)
            self.assertEqual(
                provider.requests[-1]["payload"]["feedback"]["error"],
                "candidate_comparison_requires_explicit_selection",
            )
            self.assertFalse(
                (
                    Path(fixture.request["state_dir"]) / "artifacts/final-config.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
