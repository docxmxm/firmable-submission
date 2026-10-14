"""A reviewed semantic vocabulary miss still uses the approved AST lifecycle."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from firmable_runtime.agent import _execute, _execute_once
from firmable_runtime.common import Failure
from firmable_runtime.execution import compile_config
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from firmable_runtime.workflow import (
    approve_source,
    extract_source,
    infer_source,
    review_source,
)
from test_agent import NativeFixture, ScriptedProvider
from test_config_audit import scripted_branches
from test_workflow import NoCallsProvider


class SemanticFixture(NativeFixture):
    def __init__(self, root):
        super().__init__(root, field="Caption Z")
        self.definition = (
            "Caption Z: The current name under which the company is legally registered."
        )
        (self.evidence / "definitions.md").write_text(
            self.definition + "\nrow_note: Internal reference.\n"
        )


class NameReviewWorkflowTests(unittest.TestCase):
    def test_premature_stop_requires_an_explicit_selection(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SemanticFixture(root)
            fixture.request["required_fields"] = ["entity.legal_name"]
            provider = ScriptedProvider(fixture, ["good", "stop", "select"])
            result = infer_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(result["agent_decisions"], 3)
            last_decision = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ][-1]
            self.assertEqual(
                last_decision["payload"]["feedback"]["error"],
                "selection_required_after_successful_probe",
            )
            self.assertEqual(
                last_decision["payload"]["feedback"]["eligible_candidate_ids"],
                ["good"],
            )
            self.assertTrue(result["approval_required"])
            self.assertIsNone(result["approval"])

    def test_stop_reminder_is_bounded_and_never_selects(self):
        for actions, required, limit, turns in (
            (["good", "stop", "stop"], ["entity.legal_name"], 12, 3),
            (["good", "stop", "stop"], [], 12, 3),
            (["good", "stop"], ["entity.legal_name"], 2, 2),
            (["good", "stop", "stop"], ["entity.legal_name", "entity.entity_type"], 12, 3),
        ):
            with self.subTest(actions=actions, required=required, limit=limit):
                with tempfile.TemporaryDirectory() as root:
                    fixture = SemanticFixture(root)
                    fixture.request["required_fields"] = required
                    provider = ScriptedProvider(fixture, actions)
                    with patch("firmable_runtime.agent.MAX_DECISIONS", limit):
                        result = infer_source(fixture.request, provider)
                    self.assertEqual(result["candidate_status"], "unavailable")
                    self.assertEqual(result["agent_decisions"], turns)
                    self.assertEqual(result["observations"], [])
                    self.assertEqual(result["gap"]["kind"], "agent_stop")
                    run_dir = Path(fixture.request["state_dir"])
                    self.assertFalse(list((run_dir / "artifacts").glob("validation-[0-9]*.json")))

    def test_header_subject_resolution_reaches_selected_ast(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root, field="Person Name")
            fixture.definition = "Person Name: Current legal name of the sole trader who is the subject business in this register."
            (fixture.evidence / "definitions.md").write_text(fixture.definition)
            result = infer_source(
                fixture.request, ScriptedProvider(fixture, ["good", "select"])
            )
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(result["name_review_artifact"], "name-audit-good")
            self.assertTrue(result["observations"])
            review = review_source(fixture.request["state_dir"])
            self.assertEqual(review["name_review"]["status"], "supported")

    def test_reviewed_ast_survives_validation_approval_extraction_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SemanticFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            result = infer_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(result["mapping_config"]["execution"]["kind"], "ast")
            self.assertEqual(result["name_review_artifact"], "name-audit-good")
            self.assertEqual(
                len(
                    [r for r in provider.requests if r["purpose"] == "name_role_review"]
                ),
                1,
            )
            self.assertFalse(
                any(
                    r["purpose"] in {"llm_extract", "llm_verify"}
                    for r in provider.requests
                )
            )
            run_dir = Path(fixture.request["state_dir"])
            initial = json.loads((run_dir / "artifacts/probe-good.json").read_text())
            reviewed = json.loads(
                (run_dir / "artifacts/reviewed-probe-good.json").read_text()
            )
            self.assertEqual(initial["report"]["failure_stage"], "compilation")
            self.assertEqual(initial["observations"], [])
            self.assertEqual(reviewed["report"]["errors"], [])
            self.assertTrue(reviewed["name_review_hash"])
            self.assertEqual(initial["config_hash"], reviewed["config_hash"])
            with self.assertRaises(Failure):
                compile_config(result["mapping_config"], fixture.definition)
            before = len(provider.requests)
            self.assertEqual(infer_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), before)
            runtime = Runtime(
                json.loads((run_dir / "request.json").read_text()), NoCallsProvider()
            )
            with self.assertRaises(Failure) as error:
                _execute_once(
                    runtime,
                    "reviewed-probe-good",
                    result["mapping_config"],
                    runtime.run.load_artifact("proposal-records"),
                    "proposal_after_name_review",
                )
            self.assertEqual(error.exception.kind, "checkpoint_mismatch")
            review = review_source(run_dir)
            self.assertEqual(review["name_review"]["status"], "supported")
            no_calls = NoCallsProvider()
            with self.assertRaises(Failure):
                extract_source(run_dir, provider=no_calls)
            approve_source(
                run_dir, review["review_fingerprint"], "offline-test", synthetic=True
            )
            with self.assertRaises(Failure):
                extract_source(run_dir, provider=no_calls)
            extracted = extract_source(run_dir, provider=no_calls, allow_synthetic=True)
            self.assertEqual(extracted["report"]["errors"], [])
            self.assertEqual(len(extracted["observations"]), len(fixture.rows))
            self.assertEqual(
                {r["entity"]["legal_name"] for r in extracted["observations"]},
                {r[fixture.field] for r in fixture.rows},
            )
            self.assertEqual(no_calls.calls, 0)
            self.assertEqual(
                extract_source(run_dir, provider=no_calls, allow_synthetic=True),
                extracted,
            )
            audit_path = run_dir / "artifacts/name-audit-good.json"
            audit_path.write_bytes(audit_path.read_bytes() + b"\n")
            with self.assertRaises(Failure):
                extract_source(run_dir, provider=no_calls, allow_synthetic=True)
            self.assertEqual(no_calls.calls, 0)

    def test_uncertain_review_never_reexecutes_or_selects_vocabulary_miss(self):
        class UncertainProvider(ScriptedProvider):
            def generate(self, request, call_id):
                if request["purpose"] != "name_role_review":
                    return super().generate(request, call_id)
                self.requests.append(copy.deepcopy(request))
                response = scripted_branches(
                    {
                        "reviews": [
                            {
                                "mapping_index": 0,
                                "verdict": "uncertain",
                                "evidence_quotes": [
                                    {
                                        "kind": "documentation",
                                        "quote": self.fixture.definition,
                                    }
                                ],
                                "reason": "Scripted unresolved semantic evidence.",
                                "repair_hint": "Obtain original role evidence.",
                            }
                        ]
                    },
                    request["payload"],
                )
                check_response(response, request["output_schema"])
                return {"config": response, "cost_usd": 0, "usage": {}}

        with tempfile.TemporaryDirectory() as root:
            fixture = SemanticFixture(root)
            provider = UncertainProvider(fixture, ["good", "select", "stop"])
            result = infer_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertEqual(result["observations"], [])
            run_dir = Path(fixture.request["state_dir"])
            self.assertFalse((run_dir / "artifacts/reviewed-probe-good.json").exists())
            with self.assertRaises(Failure):
                review_source(run_dir)

    def test_review_contract_failure_keeps_mapping_and_branch_feedback(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SemanticFixture(root)
            runtime = SimpleNamespace(
                docs=fixture.definition,
                input={},
                run=SimpleNamespace(state={"generated_at": "2026-10-14T00:00:00Z"}),
            )
            failure = Failure(
                "validation_error",
                "Name review evidence or structural coverage is invalid",
                review_diagnostics=[
                    {
                        "mapping_index": 0,
                        "branch_id": "/else",
                        "reason": "Companion coverage is not structurally proved",
                    }
                ],
            )
            with patch("firmable_runtime.execution.run_engine", side_effect=failure):
                observations, report = _execute(runtime, fixture.config(), [])
            self.assertEqual(observations, [])
            self.assertEqual(report["failure_stage"], "name_review")
            self.assertEqual(report["errors"][0]["mapping_index"], 0)
            self.assertEqual(report["errors"][0]["branch_id"], "/else")
            self.assertEqual(
                report["errors"][0]["reason_code"], "name_review_contract_invalid"
            )
