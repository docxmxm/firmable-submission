"""Bounded complete-response correction; ambiguous sends remain single-send."""

import copy
import tempfile
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.runtime import Runtime
from test_agent import NativeFixture


SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"const": True}}}


class FormatProvider:
    model_version = "offline-format-provider"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        outcome = self.outcomes.pop(0)
        receipt = {
            "call_id": call_id,
            "provider": "openrouter",
            "request_started": True,
            "http_status": 200,
            "response_complete": outcome != "unknown",
            "outcome": "success" if outcome != "unknown" else "unknown",
            "cost_usd": 0.01,
            "cost_basis": "Offline fixture",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        if outcome == "ok":
            return {
                "config": {"ok": True},
                "provider_receipt": receipt,
                "cost_usd": 0.01,
                "usage": receipt["usage"],
            }
        error = Failure(
            "known_failed" if outcome != "unknown" else "model_outcome_unknown", outcome
        )
        if outcome == "invalid":
            error.known_invalid_response = True
            error.response_validation = {
                "phase": "content_json",
                "message": "Extra data at line 1 column 9",
            }
            receipt["failure_kind"] = "known_invalid_response"
        elif outcome == "not_sent":
            error.definitely_not_sent = True
            receipt.update(
                request_started=False,
                response_complete=False,
                outcome="not_sent",
                http_status=None,
            )
        error.provider_receipt = receipt
        raise error


class ResponseRepairTests(unittest.TestCase):
    def runtime(self, root, provider):
        fixture = NativeFixture(root)
        return Runtime(fixture.request, provider), fixture

    def test_complete_invalid_response_corrected_and_all_costs_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["invalid", "ok"])
            runtime, _ = self.runtime(root, provider)
            self.assertEqual(
                runtime.model("agent_decision", {"decision_turn": 1}, SCHEMA),
                {"ok": True},
            )
            self.assertEqual(len(provider.requests), 2)
            self.assertIn("Extra data", provider.requests[1]["system"])
            self.assertEqual(runtime.usage()["call_count"], 2)
            self.assertEqual(runtime.usage()["total_cost_usd"], 0.02)
            self.assertEqual(runtime.usage()["total_tokens"], 30)

    def test_three_sends_limit_survives_resume_and_changed_candidate_order(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["invalid"] * 3)
            runtime, fixture = self.runtime(root, provider)
            for attempt in range(2):
                current = (
                    runtime if attempt == 0 else Runtime(fixture.request, provider)
                )
                with self.assertRaises(Failure) as error:
                    current.model(
                        "agent_decision",
                        {
                            "decision_turn": 1,
                            "context": "reordered" if attempt else "original",
                        },
                        SCHEMA,
                    )
                self.assertEqual(error.exception.kind, "known_failed")
            self.assertEqual(len(provider.requests), 3)

    def test_unknown_correction_blocks_whole_turn_after_resume(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["invalid", "unknown"])
            runtime, fixture = self.runtime(root, provider)
            for current in (runtime, None):
                current = current or Runtime(fixture.request, provider)
                with self.assertRaises(Failure) as error:
                    current.model(
                        "agent_decision",
                        {
                            "decision_turn": 1,
                            "candidate_context": len(provider.requests),
                        },
                        SCHEMA,
                    )
                self.assertEqual(error.exception.kind, "model_outcome_unknown")
            self.assertEqual(len(provider.requests), 2)

    def test_success_replays_saved_request_when_turn_context_changes(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["ok"])
            runtime, fixture = self.runtime(root, provider)
            self.assertEqual(
                runtime.model(
                    "agent_decision",
                    {"decision_turn": 1, "context": "original"},
                    SCHEMA,
                ),
                {"ok": True},
            )
            resumed = Runtime(fixture.request, provider)
            self.assertEqual(
                resumed.model(
                    "agent_decision",
                    {"decision_turn": 1, "context": "reordered"},
                    SCHEMA,
                ),
                {"ok": True},
            )
            self.assertEqual(len(provider.requests), 1)

    def test_definitely_not_sent_does_not_trigger_format_correction(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["not_sent"])
            runtime, _ = self.runtime(root, provider)
            with self.assertRaises(Failure) as error:
                runtime.model("inspect", {}, SCHEMA)
            self.assertEqual(error.exception.kind, "known_failed")
            self.assertEqual(len(provider.requests), 1)

    def test_durable_failure_recovers_crash_before_journal_transition(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FormatProvider(["invalid", "ok"])
            runtime, fixture = self.runtime(root, provider)
            original = runtime.run.artifact

            def crash(name, value):
                ref = original(name, value)
                if name.startswith("model-failure-"):
                    raise RuntimeError("Crash after durable failure")
                return ref

            runtime.run.artifact = crash
            with self.assertRaisesRegex(RuntimeError, "durable failure"):
                runtime.model("agent_decision", {"decision_turn": 1}, SCHEMA)
            resumed = Runtime(fixture.request, provider)
            self.assertEqual(
                resumed.model("agent_decision", {"decision_turn": 1}, SCHEMA),
                {"ok": True},
            )
            self.assertEqual(len(provider.requests), 2)


if __name__ == "__main__":
    unittest.main()
