"""Observed-link acquisition is optional, durable, and feeds original evidence to infer."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.common import Failure
from firmable_runtime.workflow import (
    approve_source,
    extract_source,
    infer_source,
    review_source,
)
from test_acquisition import action
from test_agent import NativeFixture, ScriptedProvider


class CombinedProvider(ScriptedProvider):
    def __init__(self, fixture, reading):
        super().__init__(fixture, ["good", "select"])
        self.reading = iter(reading)

    def generate(self, request, call_id):
        if request["purpose"] != "source_evidence_decision":
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        value = next(self.reading)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(request["payload"])
        return {"config": value, "cost_usd": 0, "usage": {}}


def fetch_link(payload):
    return action(
        "fetch",
        reference_id=payload["initial_capture"]["references"][0]["reference_id"],
    )


def read_page(payload):
    return action(
        "read", asset_id=payload["history"][-1]["result"]["asset"]["asset_id"]
    )


class AcquisitionWorkflowTests(unittest.TestCase):
    def fixture(self, tmp, missing=False):
        fixture = NativeFixture(tmp, field="title_42")
        fixture.request["catalogue_metadata"]["arbitrary_resources"] = [
            {"descriptive_link": "https://agency.example/registry/help"}
        ]
        if missing:
            (fixture.evidence / "definitions.md").write_text(
                "Registry table; field definitions available from publisher.\n"
            )
        return fixture

    def test_explicit_local_evidence_mode_skips_acquisition_and_binds_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp)
            fixture.request["source_acquisition_enabled"] = False
            provider = CombinedProvider(fixture, [])
            with patch("firmable_runtime.workflow._acquire_for_inference") as acquire:
                result = infer_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertFalse(acquire.called)
                self.assertNotIn("source_acquisition", result)
                self.assertFalse(any(
                    request["purpose"] == "source_evidence_decision"
                    for request in provider.requests
                ))
                root = Path(fixture.request["state_dir"])
                self.assertFalse((root / "acquisition").exists())
                self.assertFalse((root / "capture").exists())
                saved = json.loads((root / "request.json").read_text())
                self.assertEqual(saved["evidence_root"], str(fixture.evidence.resolve()))
                self.assertEqual(saved["source_acquisition_policy"], {
                    "mode": "disabled_local_evidence_only"
                })
                review = review_source(root)
                approve_source(root, review["review_fingerprint"], "test", synthetic=True)
                extracted = extract_source(root, provider=provider, allow_synthetic=True)
                self.assertEqual(len(extracted["observations"]), len(fixture.rows))
                changed = {**fixture.request, "source_acquisition_enabled": True}
                with self.assertRaises(Failure):
                    infer_source(changed, provider)
                self.assertFalse(acquire.called)

    def test_acquisition_switch_rejects_non_boolean_before_any_model_call(self):
        for value in ("false", 0, None):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                fixture = self.fixture(tmp)
                fixture.request["source_acquisition_enabled"] = value
                provider = CombinedProvider(fixture, [])
                with self.assertRaises(Failure):
                    infer_source(fixture.request, provider)
                self.assertEqual(provider.requests, [])

    def test_agent_fetches_original_then_repairs_infers_and_approves_bound_snapshot(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp, missing=True)
            provider = CombinedProvider(
                fixture, [fetch_link, read_page, action("finish")]
            )
            provider.actions = ["bad", "good", "select"]
            original = fixture.native.read_bytes()
            page = ("<main>" + fixture.definition + "</main>").encode()
            with patch(
                "firmable_runtime.source_acquisition._fetch_http",
                return_value={
                    "url": "https://agency.example/registry/help",
                    "status": 200,
                    "media_type": "text/html",
                    "body": page,
                },
            ) as network:
                result = infer_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertIsNone(result["approval"])
                self.assertEqual(network.call_count, 1)
                self.assertEqual(result["source_acquisition"]["excerpt_count"], 1)
                planner = next(
                    r for r in provider.requests if r["purpose"] == "agent_decision"
                )
                self.assertIn(
                    fixture.definition, planner["payload"]["source_documentation"]
                )
                self.assertEqual(
                    planner["payload"]["catalogue_metadata"],
                    fixture.request["catalogue_metadata"],
                )
                root = Path(fixture.request["state_dir"])
                request = json.loads((root / "request.json").read_text())
                snapshot = Path(request["evidence_root"])
                self.assertEqual(
                    (snapshot / fixture.native.name).read_bytes(), original
                )
                self.assertEqual(fixture.native.read_bytes(), original)
                export = (root / "acquisition/source-evidence.md").read_text()
                self.assertNotIn("MODEL_DIAGNOSTIC_NOT_SOURCE", export)
                self.assertNotIn('"json_pointer"', export)
                calls = len(provider.requests)
                self.assertEqual(infer_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)
                self.assertEqual(network.call_count, 1)
                review = review_source(root)
                self.assertEqual(review["source_acquisition"]["excerpt_count"], 1)
                approve_source(
                    root, review["review_fingerprint"], "test", synthetic=True
                )
                extracted = extract_source(
                    root, provider=provider, allow_synthetic=True
                )
                self.assertEqual(len(extracted["observations"]), len(fixture.rows))
                asset = next(
                    a
                    for a in json.loads((root / "capture/manifest.json").read_text())[
                        "assets"
                    ]
                    if a["kind"] == "original"
                )
                (root / "capture" / asset["path"]).write_bytes(b"changed")
                with self.assertRaises((Failure, ValueError)):
                    review_source(root)

    def test_agent_can_finish_without_any_fetch_or_evidence_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp)
            provider = CombinedProvider(fixture, [action("finish")])
            with patch("firmable_runtime.source_acquisition._fetch_http") as network:
                result = infer_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["source_acquisition"]["excerpt_count"], 0)
                self.assertFalse(network.called)
                self.assertFalse(
                    (Path(fixture.request["state_dir"]) / "evidence").exists()
                )

    def test_navigation_and_model_reason_cannot_supply_field_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp, missing=True)
            fixture.request["catalogue_metadata"]["analysis"] = fixture.definition

            def read_navigation(payload):
                return action(
                    "read",
                    asset_id=payload["initial_capture"]["assets"][0]["asset_id"],
                    reason=fixture.definition,
                )

            provider = CombinedProvider(fixture, [read_navigation, action("finish")])
            # Acquisition cannot turn navigation or a model explanation into a
            # documentary definition, regardless of the later planner response.
            from firmable_runtime.workflow import _acquire_for_inference

            Path(fixture.request["state_dir"]).mkdir()
            effective, result = _acquire_for_inference(fixture.request, provider)
            self.assertEqual(result["excerpt_count"], 0)
            self.assertEqual(effective["evidence_root"], str(fixture.evidence))
            self.assertNotIn(
                fixture.definition, Path(result["export_path"]).read_text()
            )

    def test_fetch_failure_is_recorded_and_existing_input_remains_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp)
            provider = CombinedProvider(fixture, [fetch_link, action("finish")])
            with patch(
                "firmable_runtime.source_acquisition._fetch_http",
                side_effect=OSError("unavailable"),
            ) as network:
                result = infer_source(fixture.request, provider)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["source_acquisition"]["excerpt_count"], 0)
                infer_source(fixture.request, provider)
                self.assertEqual(network.call_count, 1)

    def test_unknown_acquisition_outcome_is_never_resent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.fixture(tmp)
            provider = CombinedProvider(fixture, [TimeoutError("unknown")])
            with patch("firmable_runtime.source_acquisition._fetch_http") as network:
                for _ in range(2):
                    with self.assertRaises(Failure) as raised:
                        infer_source(fixture.request, provider)
                    self.assertEqual(raised.exception.kind, "model_outcome_unknown")
                self.assertEqual(len(provider.requests), 1)
                self.assertFalse(network.called)
                self.assertFalse(
                    (
                        Path(fixture.request["state_dir"]) / "checkpoint.internal.json"
                    ).exists()
                )


if __name__ == "__main__":
    unittest.main()
