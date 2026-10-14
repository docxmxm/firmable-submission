"""Resume durable local actions without rerunning them or resending model calls."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.agent import onboard_source
from firmable_runtime.runtime import Runtime
from test_agent import NativeFixture, ScriptedProvider


class RecoveryTests(unittest.TestCase):
    def test_resume_after_probe_was_saved_before_candidate_state(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            original = Runtime.event

            def interrupt(runtime, step, *args, **kwargs):
                if step == "candidate_probe":
                    raise RuntimeError("Crash after durable probe")
                return original(runtime, step, *args, **kwargs)

            with patch.object(Runtime, "event", interrupt):
                with self.assertRaisesRegex(RuntimeError, "durable probe"):
                    onboard_source(fixture.request, provider)
            run = Path(fixture.request["state_dir"])
            saved = json.loads((run / "artifacts/probe-good.json").read_text())
            checkpoint = json.loads((run / "checkpoint.internal.json").read_text())
            self.assertTrue(saved["observations"])
            self.assertEqual(checkpoint["agent"]["candidates"], {})
            self.assertEqual(len(provider.requests), 5)

            recovered = onboard_source(fixture.request, provider)
            self.assertEqual(recovered["status"], "complete")
            self.assertEqual(
                len(provider.requests), 7
            )  # The recovered probe gets its name review, then select.
            self.assertEqual(
                json.loads((run / "artifacts/probe-good.json").read_text()), saved
            )
            self.assertEqual(
                {row["entity"]["legal_name"] for row in recovered["observations"]},
                {row["raw_record"][fixture.field] for row in recovered["observations"]},
            )

    def test_resume_after_validation_was_saved_before_selection_state(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            original = Runtime.event

            def interrupt(runtime, step, *args, **kwargs):
                if step == "validation":
                    raise RuntimeError("Crash after durable validation")
                return original(runtime, step, *args, **kwargs)

            with patch.object(Runtime, "event", interrupt):
                with self.assertRaisesRegex(RuntimeError, "durable validation"):
                    onboard_source(fixture.request, provider)
            run = Path(fixture.request["state_dir"])
            saved = json.loads((run / "artifacts/validation-1.json").read_text())
            self.assertTrue(saved["observations"])
            self.assertEqual(len(provider.requests), 7)

            recovered = onboard_source(fixture.request, provider)
            self.assertEqual(recovered["status"], "complete")
            self.assertEqual(recovered["observations"], saved["observations"])
            self.assertEqual(len(provider.requests), 7)

    def test_resume_after_final_result_was_saved_before_result_pointer(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            provider = ScriptedProvider(fixture, ["good", "select"])
            original = Runtime.artifact
            saved = {}

            def interrupt(runtime, name, value):
                ref = original(runtime, name, value)
                if name == "onboard-result":
                    saved["result"] = copy.deepcopy(value)
                    raise RuntimeError("Crash after durable final result")
                return ref

            with patch.object(Runtime, "artifact", interrupt):
                with self.assertRaisesRegex(RuntimeError, "durable final result"):
                    onboard_source(fixture.request, provider)
            self.assertEqual(len(provider.requests), 7)

            recovered = onboard_source(fixture.request, provider)
            self.assertEqual(recovered, saved["result"])
            self.assertEqual(len(provider.requests), 7)
            self.assertEqual(onboard_source(fixture.request, provider), recovered)


if __name__ == "__main__":
    unittest.main()
