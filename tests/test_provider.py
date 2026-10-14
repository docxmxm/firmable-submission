"""Mock the CLI boundary: distinguish known bad output from an unknown send."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.common import Failure
from firmable_runtime.provider import CcVibeProvider
from firmable_runtime.store import DurableRun


REQUEST = {
    "system": "Return the supported name.",
    "payload": {"name": "Aster Pty Ltd"},
    "output_schema": {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    },
}


def cli_result(value, usage=None):
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {"response_json": json.dumps(value)},
        "total_cost_usd": 0.07,
        "usage": usage,
    }
    return subprocess.CompletedProcess(
        ["mock-claude"], 0, json.dumps(envelope).encode(), b""
    )


class ProviderTests(unittest.TestCase):
    def test_explicit_cli_success_but_invalid_inner_schema_is_known_failed_with_cost(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            run = DurableRun(root, {"test": "schema_failure"})
            call_id = run.prepare_call("extract", REQUEST)
            provider = CcVibeProvider()
            with (
                patch.object(
                    provider, "_environment", return_value=({}, "api.ccvibe.test")
                ),
                patch(
                    "firmable_runtime.provider.subprocess.run",
                    return_value=cli_result(
                        {"name": 123}, {"input_tokens": 10, "output_tokens": 5}
                    ),
                ) as launch,
            ):
                with self.assertRaises(Failure) as error:
                    run.call(call_id, provider)
                self.assertEqual(error.exception.kind, "known_failed")
                resumed = DurableRun(
                    root,
                    {"test": "schema_failure"},
                    Path(root) / "checkpoint.internal.json",
                )
                entry = resumed.state["calls"][0]
                self.assertEqual(entry["status"], "known_failed")
                self.assertTrue(entry["known_invalid_response"])
                receipt = resumed.load_artifact(entry["provider_receipt_artifact"])
                self.assertEqual(receipt["cli_reported"]["total_cost_usd"], 0.07)
                self.assertEqual(receipt["cli_reported"]["usage"]["output_tokens"], 5)
                self.assertNotIn("structured_output", receipt)
                with self.assertRaises(Failure):
                    resumed.call(call_id, provider)
                launch.assert_called_once()

    def test_timeout_or_empty_output_is_unknown_and_never_resent(self):
        for outcome in (
            subprocess.TimeoutExpired("mock-claude", 0.01),
            subprocess.CompletedProcess(["mock-claude"], 0, b"", b""),
        ):
            with (
                self.subTest(outcome=type(outcome).__name__),
                tempfile.TemporaryDirectory() as root,
            ):
                run = DurableRun(root, {"test": "unknown"})
                call_id = run.prepare_call("extract", REQUEST)
                provider = CcVibeProvider()
                mock_args = (
                    {"side_effect": outcome}
                    if isinstance(outcome, Exception)
                    else {"return_value": outcome}
                )
                with (
                    patch.object(
                        provider, "_environment", return_value=({}, "api.ccvibe.test")
                    ),
                    patch(
                        "firmable_runtime.provider.subprocess.run", **mock_args
                    ) as launch,
                ):
                    for attempt in range(2):
                        current = (
                            run
                            if attempt == 0
                            else DurableRun(
                                root,
                                {"test": "unknown"},
                                Path(root) / "checkpoint.internal.json",
                            )
                        )
                        with self.assertRaises(Failure) as error:
                            current.call(call_id, provider)
                        self.assertEqual(error.exception.kind, "model_outcome_unknown")
                    launch.assert_called_once()

    def test_disabled_api_is_definitely_not_sent(self):
        with (
            patch.dict(os.environ, {"FIRMABLE_MVP_API": "0"}),
            patch("firmable_runtime.provider.subprocess.run") as launch,
        ):
            with self.assertRaises(Failure) as error:
                CcVibeProvider().generate(REQUEST, "offline-call")
            self.assertEqual(error.exception.kind, "model_disabled")
            self.assertTrue(error.exception.definitely_not_sent)
            launch.assert_not_called()

    def test_malformed_profile_is_definitely_not_sent(self):
        for settings in ([], {"env": []}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as root:
                profile = Path(root) / ".claude/mvp-api/settings.json"
                profile.parent.mkdir(parents=True)
                profile.write_text(json.dumps(settings))
                profile.chmod(0o600)
                with (
                    patch.dict(os.environ, {"FIRMABLE_MVP_API": "1"}),
                    patch(
                        "firmable_runtime.provider.Path.home", return_value=Path(root)
                    ),
                    patch("firmable_runtime.provider.subprocess.run") as launch,
                ):
                    with self.assertRaises(Failure) as error:
                        CcVibeProvider().generate(REQUEST, "offline-call")
                    self.assertEqual(error.exception.kind, "model_disabled")
                    self.assertTrue(error.exception.definitely_not_sent)
                    launch.assert_not_called()

    def test_missing_usage_does_not_discard_success_or_known_cost(self):
        provider = CcVibeProvider()
        with (
            patch.object(
                provider, "_environment", return_value=({}, "api.ccvibe.test")
            ),
            patch(
                "firmable_runtime.provider.subprocess.run",
                return_value=cli_result({"name": "Aster Pty Ltd"}),
            ),
        ):
            result = provider.generate(REQUEST, "offline-call")
        self.assertEqual(result["config"], {"name": "Aster Pty Ltd"})
        self.assertEqual(result["cost_usd"], 0.07)
        self.assertEqual(result["usage"], {})

    def test_missing_cli_is_definitely_not_sent(self):
        provider = CcVibeProvider()
        with (
            patch.object(
                provider, "_environment", return_value=({}, "api.ccvibe.test")
            ),
            patch(
                "firmable_runtime.provider.subprocess.run",
                side_effect=FileNotFoundError("CLI missing"),
            ),
        ):
            with self.assertRaises(Failure) as error:
                provider.generate(REQUEST, "offline-call")
        self.assertTrue(getattr(error.exception, "definitely_not_sent", False))


if __name__ == "__main__":
    unittest.main()
