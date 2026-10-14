"""Exhausted known rule-format failures may prompt a fresh bounded candidate.

All provider receipts are synthetic. Unknown outcomes and source semantics remain
independent gates; a response-format repair never authorizes a mapping.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure
from test_agent import NativeFixture, ScriptedProvider, control


class RuleFailureProvider(ScriptedProvider):
    def __init__(self, fixture, outcomes, *, select=True):
        actions = [
            control("probe", candidate_id="malformed", backend="ast"),
            control("probe", candidate_id="fresh", backend="ast"),
            control("select", candidate_id="fresh"),
        ]
        if not select:
            actions.append(
                control("stop", reason="Semantic evidence remains insufficient.")
            )
        super().__init__(fixture, actions)
        self.candidate_configs = {
            ident: fixture.config() for ident in ("malformed", "fresh")
        }
        self.outcomes = list(outcomes)
        self.failed_rule_call_ids = []

    def generate(self, request, call_id):
        if (
            request["purpose"] != "candidate_rules"
            or request["payload"]["candidate_id"] != "malformed"
        ):
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        self.failed_rule_call_ids.append(call_id)
        outcome = self.outcomes.pop(0)
        if outcome == "unknown":
            raise TimeoutError("Rule response may have been processed")
        error = Failure("known_failed", "Synthetic rule response failure")
        if outcome == "not_sent":
            error.definitely_not_sent = True
            raise error
        error.known_invalid_response = True
        error.response_validation = {
            "phase": "schema",
            "rule": "required",
            "path": ["expressions", 0, "ast"],
            "message": "Complete model response lacks a required AST member",
        }
        error.provider_receipt = {
            "call_id": call_id,
            "provider": "openrouter",
            "request_started": True,
            "http_status": 200,
            "response_complete": True,
            "outcome": "success",
            "failure_kind": "known_invalid_response",
            "cost_usd": 0,
            "cost_basis": "Offline synthetic receipt",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        raise error


def checkpoint(fixture):
    return json.loads(
        (Path(fixture.request["state_dir"]) / "checkpoint.internal.json").read_text()
    )


class RuleResponseRecoveryTests(unittest.TestCase):
    def test_three_known_invalid_rules_allow_fresh_candidate_and_durable_resume(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = RuleFailureProvider(fixture, ["invalid"] * 3)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["candidate_status"], "validated")
            self.assertTrue(result["observations"])
            self.assertTrue(
                all(
                    row["entity"]["legal_name"] == row["raw_record"][fixture.field]
                    for row in result["observations"]
                )
            )
            self.assertEqual(len(set(provider.failed_rule_call_ids)), 3)
            decisions = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ]
            feedback = decisions[1]["payload"]["feedback"]
            self.assertEqual(feedback["candidate_id"], "malformed")
            self.assertTrue(feedback["known_invalid_response"])
            self.assertEqual(
                feedback["response_validation"]["path"], ["expressions", 0, "ast"]
            )
            state = checkpoint(fixture)
            failed = [
                c
                for c in state["calls"]
                if c["call_id"] in provider.failed_rule_call_ids
            ]
            self.assertEqual([c["status"] for c in failed], ["known_failed"] * 3)
            self.assertNotIn("malformed", state["agent"]["candidates"])
            self.assertEqual(
                state["agent"]["candidates"]["fresh"]["validation_status"], "passed"
            )
            artifacts = Path(fixture.request["state_dir"]) / "artifacts"
            self.assertFalse((artifacts / "candidate-malformed.json").exists())
            self.assertFalse(
                (artifacts / "candidate-execution-malformed.json").exists()
            )
            count = len(provider.requests)
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), count)

    def test_unknown_during_rule_correction_never_becomes_planner_feedback_or_resend(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = RuleFailureProvider(fixture, ["invalid", "unknown"])
            for attempt in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                if attempt == 0:
                    count = len(provider.requests)
                else:
                    self.assertEqual(len(provider.requests), count)
            self.assertEqual(len(provider.failed_rule_call_ids), 2)
            self.assertEqual(
                sum(r["purpose"] == "agent_decision" for r in provider.requests), 1
            )
            self.assertNotIn(
                "fresh", checkpoint(fixture)["agent"].get("candidates", {})
            )

    def test_other_known_provider_failure_is_not_a_format_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = RuleFailureProvider(fixture, ["not_sent"])
            with self.assertRaises(Failure) as caught:
                onboard_source(fixture.request, provider)
            self.assertEqual(caught.exception.kind, "known_failed")
            self.assertFalse(caught.exception.details["known_invalid_response"])
            self.assertEqual(len(provider.failed_rule_call_ids), 1)
            self.assertEqual(
                sum(r["purpose"] == "agent_decision" for r in provider.requests), 1
            )

    def test_fresh_well_formed_rules_still_cannot_bypass_foreign_subject_semantics(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root, field="director_name")
            fixture.definition = "director_name: Legal name of the director."
            (fixture.evidence / "definitions.md").write_text(
                fixture.definition
                + "\nrow_note: Internal row reference.\nLicence: CC BY 4.0\n"
            )
            provider = RuleFailureProvider(fixture, ["invalid"] * 3, select=False)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertNotEqual(result["candidate_status"], "validated")
            self.assertEqual(result["observations"], [])
            candidate = checkpoint(fixture)["agent"]["candidates"]["fresh"]
            self.assertFalse(candidate["eligible"])
            self.assertTrue(candidate["feedback"]["errors"])
            self.assertTrue(
                any(
                    error.get("reason_code") in {"semantic_rule", "subject_attribution"}
                    for error in candidate["feedback"]["errors"]
                )
            )
            self.assertEqual(len(provider.failed_rule_call_ids), 3)


if __name__ == "__main__":
    unittest.main()
