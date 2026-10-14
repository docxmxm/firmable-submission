"""Known table-scope mistakes get bounded feedback; unknown calls never resend."""

import copy
import csv
import tempfile
import unittest
from unittest.mock import patch

from firmable_runtime.agent import _check_table_scope
from firmable_runtime.common import Failure, canonical
from firmable_runtime.native import public_structure
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from test_agent import NativeFixture


class ScopeSequenceProvider:
    model_version = "offline-scope-correction-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("Unexpected extra model call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        check_response(response, request["output_schema"])
        return {"config": response, "usage": {}, "cost_usd": 0.0}


class TableScopeRetryTests(unittest.TestCase):
    def fixture(self, root):
        fixture = NativeFixture(root)
        with (fixture.evidence / "zzz-reference.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["reference_text"])
            writer.writeheader()
            writer.writerow({"reference_text": "Reference only"})
        return fixture

    def runtime(self, fixture, provider):
        runtime = Runtime(fixture.request, provider)
        structure = [
            {"table_id": i, **item}
            for i, item in enumerate(public_structure(runtime.tables))
        ]
        ref = runtime.artifact("discovery", {"tables": structure})
        return runtime, structure, ref

    def check_scope(self, runtime, structure, ref):
        return _check_table_scope(
            runtime,
            {"selected_table_ids": [0], "reason": "The first table holds entities."},
            structure,
            {},
            ref,
        )

    def response(self, quote="reference_text"):
        return {
            "selected_table_ids": [0],
            "reason": "Keep the business rows and exclude reference material.",
            "exclusions": [
                {
                    "table_id": 1,
                    "reason": "Reference material based on this table's structure.",
                    "evidence_quote": quote,
                }
            ],
        }

    def test_paraphrased_structure_quote_gets_exact_quote_correction(self):
        bad = self.response("File 'zzz-reference.csv' contains reference_text.")
        good = self.response()
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = ScopeSequenceProvider([bad, good])
            runtime, structure, ref = self.runtime(fixture, provider)
            self.assertEqual(self.check_scope(runtime, structure, ref), [0])
            self.assertEqual(len(provider.requests), 2)
            first, correction = provider.requests
            self.assertEqual(first["payload"]["structures"], correction["payload"]["structures"])
            self.assertEqual(first["payload"]["source_documentation"], correction["payload"]["source_documentation"])
            feedback = canonical(correction).decode()
            self.assertIn("quotation", feedback.lower())
            self.assertIn(bad["exclusions"][0]["evidence_quote"], feedback)
            ledger = runtime.run.load_artifact("table-scope")
            self.assertEqual(ledger["exclusions"], good["exclusions"])
            self.assertEqual(ledger["selected_record_count"], 8)
            self.assertEqual(ledger["excluded_record_count"], 1)

    def test_partition_error_can_be_corrected_without_auto_inclusion(self):
        bad = self.response()
        bad["exclusions"] = []
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = ScopeSequenceProvider([bad, self.response()])
            runtime, structure, ref = self.runtime(fixture, provider)
            self.assertEqual(self.check_scope(runtime, structure, ref), [0])
            self.assertEqual(len(provider.requests), 2)
            self.assertIn("partition", canonical(provider.requests[1]).decode().lower())
            self.assertEqual(runtime.run.load_artifact("table-scope")["included_table_ids"], [0])

    def test_repeated_invalid_quotes_stop_after_two_corrections(self):
        bad = self.response("A fabricated table definition.")
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = ScopeSequenceProvider([bad, bad, bad])
            for _ in range(2):
                runtime, structure, ref = self.runtime(fixture, provider)
                with self.assertRaises(Failure) as caught:
                    self.check_scope(runtime, structure, ref)
                self.assertEqual(caught.exception.kind, "validation_error")
                self.assertNotIn("table-scope", runtime.run.state["artifacts"])
            self.assertEqual(len(provider.requests), 3)

    def test_other_table_quote_is_not_accepted_without_correction(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            bad = self.response("register.csv")
            provider = ScopeSequenceProvider([bad, bad, bad])
            runtime, structure, ref = self.runtime(fixture, provider)
            self.assertNotIn("register.csv", canonical(structure[1]).decode())
            with self.assertRaises(Failure):
                self.check_scope(runtime, structure, ref)
            self.assertEqual(len(provider.requests), 3)
            self.assertNotIn("table-scope", runtime.run.state["artifacts"])

    def test_unknown_correction_is_durable_and_never_resent(self):
        bad = self.response("A fabricated table definition.")
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = ScopeSequenceProvider([bad, TimeoutError("Outcome unknown")])
            for _ in range(2):
                runtime, structure, ref = self.runtime(fixture, provider)
                with self.assertRaises(Failure) as caught:
                    self.check_scope(runtime, structure, ref)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                self.assertNotIn("table-scope", runtime.run.state["artifacts"])
            self.assertEqual(len(provider.requests), 2)

    def test_resume_before_scope_ledger_reuses_completed_correction(self):
        bad = self.response("A fabricated table definition.")
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = ScopeSequenceProvider([bad, self.response()])
            runtime, structure, ref = self.runtime(fixture, provider)
            original = runtime.artifact

            def interrupt_ledger(name, value):
                if name == "table-scope":
                    raise RuntimeError("Injected host interruption")
                return original(name, value)

            with patch.object(runtime, "artifact", side_effect=interrupt_ledger):
                with self.assertRaisesRegex(RuntimeError, "Injected host interruption"):
                    self.check_scope(runtime, structure, ref)
            self.assertEqual(len(provider.requests), 2)
            resumed, structure, ref = self.runtime(fixture, provider)
            self.assertEqual(self.check_scope(resumed, structure, ref), [0])
            self.assertEqual(len(provider.requests), 2)


if __name__ == "__main__":
    unittest.main()
