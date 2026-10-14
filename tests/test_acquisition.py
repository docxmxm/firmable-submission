"""Original evidence export and durable acquisition, with no external calls."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.acquisition import acquire_source_evidence
from firmable_runtime.common import Failure, atomic_json, byte_hash, strict_json
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from test_agent import NativeFixture


REASON = "MODEL_DIAGNOSTIC_NOT_SOURCE: invented registered-name conclusion"
PAGE = (
    "<main>Source catalogue</main>\n"
    '<script type="application/json">{"column":"MarketTitle","label":"Operating name"}</script>\n'
    '<script src="/assets/cell.js"></script>'
)
COMPONENT = 'export function show(row){return "Operating as "+row["MarketTitle"]}'


def action(kind, **values):
    return {
        "action": kind,
        "query": "",
        "asset_id": "",
        "reference_id": "",
        "start": 0,
        "max_chars": 12000,
        "reference_offset": 0,
        "reason": REASON,
        **values,
    }


def fetch_seen(payload):
    refs = payload["history"][-1]["result"]["matches"][0]["references"]
    return action("fetch", reference_id=refs[0]["reference_id"])


def read_fetched(payload):
    asset = payload["history"][-1]["result"]["asset"]
    return action("read", asset_id=asset["asset_id"])


class ReadingProvider:
    model_version = "offline-original-evidence-reader"

    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        response = next(self.actions)
        if isinstance(response, Exception):
            raise response
        response = response(request["payload"]) if callable(response) else response
        check_response(response, request["output_schema"])
        return {"config": response, "cost_usd": 0, "usage": {}}


def capture_fixture(root):
    fixture = NativeFixture(root, field="MarketTitle")
    seed = fixture.root / "publisher.html"
    seed.write_text(PAGE)
    seeds = [
        {
            "url": "https://publisher.example/catalogue",
            "path": str(seed),
            "sha256": byte_hash(seed.read_bytes()),
            "media_type": "text/html",
        }
    ]
    return fixture, seeds, fixture.root / "capture"


def fake_fetch(url, maximum):
    return {
        "url": url,
        "status": 200,
        "media_type": "text/javascript",
        "body": COMPONENT.encode(),
    }


class AcquisitionTests(unittest.TestCase):
    def test_scripted_reading_exports_exact_originals_and_resumes_without_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            provider = ReadingProvider(
                [
                    action("search", query="MarketTitle"),
                    fetch_seen,
                    read_fetched,
                    action("finish"),
                ]
            )
            with patch(
                "firmable_runtime.source_acquisition._fetch_http",
                side_effect=fake_fetch,
            ) as network:
                result = acquire_source_evidence(
                    fixture.request, provider, capture, seeds
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["excerpt_count"], 2)
                self.assertEqual(len(provider.requests), 4)
                self.assertEqual(network.call_count, 1)
                export = Path(result["export_path"]).read_text()
                self.assertIn(PAGE, export)
                self.assertIn(COMPONENT, export)
                self.assertNotIn(REASON, export)
                manifest = strict_json((capture / "manifest.json").read_bytes())
                receipt = strict_json(
                    (
                        Path(fixture.request["state_dir"])
                        / "artifacts/source-evidence-export.json"
                    ).read_bytes()
                )
                for excerpt in receipt["excerpts"]:
                    asset = next(
                        a
                        for a in manifest["assets"]
                        if a["asset_id"] == excerpt["asset_id"]
                    )
                    raw = (capture / asset["path"]).read_bytes()
                    self.assertEqual(excerpt["sha256"], byte_hash(raw))
                    self.assertEqual(
                        excerpt["text"], raw.decode()[excerpt["start"] : excerpt["end"]]
                    )
                    self.assertEqual(
                        excerpt["text_sha256"], byte_hash(excerpt["text"].encode())
                    )
                self.assertEqual(
                    acquire_source_evidence(fixture.request, provider, capture, seeds),
                    result,
                )
                self.assertEqual(len(provider.requests), 4)
                self.assertEqual(network.call_count, 1)

    def test_resume_after_fetch_before_tool_receipt_reuses_captured_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            provider = ReadingProvider(
                [
                    action("search", query="MarketTitle"),
                    fetch_seen,
                    read_fetched,
                    action("finish"),
                ]
            )
            original = Runtime.artifact

            def crash_before_receipt(runtime, name, value):
                if name == "acquisition-tool-1":
                    raise RuntimeError("simulated interruption after fetch")
                return original(runtime, name, value)

            with patch(
                "firmable_runtime.source_acquisition._fetch_http",
                side_effect=fake_fetch,
            ) as network:
                with patch.object(Runtime, "artifact", crash_before_receipt):
                    with self.assertRaisesRegex(RuntimeError, "after fetch"):
                        acquire_source_evidence(
                            fixture.request, provider, capture, seeds
                        )
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(network.call_count, 1)
                result = acquire_source_evidence(
                    fixture.request, provider, capture, seeds
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(provider.requests), 4)
                self.assertEqual(network.call_count, 1)

    def test_seed_export_and_manifest_changes_are_rejected_without_new_calls(self):
        for changed in ("seed", "export", "manifest"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                fixture, seeds, capture = capture_fixture(tmp)
                provider = ReadingProvider(
                    [action("search", query="MarketTitle"), action("finish")]
                )
                with patch(
                    "firmable_runtime.source_acquisition._fetch_http"
                ) as network:
                    result = acquire_source_evidence(
                        fixture.request, provider, capture, seeds
                    )
                    if changed == "seed":
                        Path(seeds[0]["path"]).write_text("changed source")
                    elif changed == "export":
                        Path(result["export_path"]).write_text("changed evidence")
                    else:
                        manifest = strict_json((capture / "manifest.json").read_bytes())
                        manifest["omissions"].append({"kind": "external mutation"})
                        atomic_json(capture / "manifest.json", manifest)
                    with self.assertRaises(Failure) as error:
                        acquire_source_evidence(
                            fixture.request, provider, capture, seeds
                        )
                    self.assertEqual(error.exception.kind, "checkpoint_mismatch")
                    self.assertEqual(len(provider.requests), 2)
                    network.assert_not_called()

    def test_decision_limit_exports_partial_without_claiming_completed_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            fixture.request["source_acquisition_max_steps"] = 1
            provider = ReadingProvider([action("search", query="MarketTitle")])
            with patch("firmable_runtime.source_acquisition._fetch_http") as network:
                result = acquire_source_evidence(
                    fixture.request, provider, capture, seeds
                )
                self.assertEqual(result["status"], "partial")
                self.assertFalse(result["acquisition_complete"])
                self.assertEqual(result["excerpt_count"], 1)
                self.assertEqual(
                    acquire_source_evidence(fixture.request, provider, capture, seeds),
                    result,
                )
                self.assertEqual(len(provider.requests), 1)
                network.assert_not_called()

    def test_unknown_model_outcome_stops_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            provider = ReadingProvider([TimeoutError("unknown model outcome")])
            with patch("firmable_runtime.source_acquisition._fetch_http") as network:
                for _ in range(2):
                    with self.assertRaises(Failure) as error:
                        acquire_source_evidence(
                            fixture.request, provider, capture, seeds
                        )
                    self.assertEqual(error.exception.kind, "model_outcome_unknown")
                self.assertEqual(len(provider.requests), 1)
                network.assert_not_called()

    def test_pending_read_does_not_authorize_manifest_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            provider = ReadingProvider(
                [action("search", query="MarketTitle"), action("finish")]
            )
            with patch(
                "firmable_runtime.acquisition._tool",
                side_effect=RuntimeError("before read"),
            ):
                with self.assertRaisesRegex(RuntimeError, "before read"):
                    acquire_source_evidence(fixture.request, provider, capture, seeds)
            manifest = strict_json((capture / "manifest.json").read_bytes())
            manifest["assets"][0]["url"] = (
                "https://different.example/forged-attribution"
            )
            atomic_json(capture / "manifest.json", manifest)
            with self.assertRaises(Failure) as error:
                acquire_source_evidence(fixture.request, provider, capture, seeds)
            self.assertEqual(error.exception.kind, "checkpoint_mismatch")
            self.assertEqual(len(provider.requests), 1)

    def test_pending_fetch_cannot_relabel_the_newly_captured_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, seeds, capture = capture_fixture(tmp)
            provider = ReadingProvider(
                [
                    action("search", query="MarketTitle"),
                    fetch_seen,
                    read_fetched,
                    action("finish"),
                ]
            )
            original = Runtime.artifact

            def crash_before_receipt(runtime, name, value):
                if name == "acquisition-tool-1":
                    raise RuntimeError("simulated interruption after fetch")
                return original(runtime, name, value)

            with patch(
                "firmable_runtime.source_acquisition._fetch_http",
                side_effect=fake_fetch,
            ) as network:
                with patch.object(Runtime, "artifact", crash_before_receipt):
                    with self.assertRaisesRegex(RuntimeError, "after fetch"):
                        acquire_source_evidence(
                            fixture.request, provider, capture, seeds
                        )
                manifest = strict_json((capture / "manifest.json").read_bytes())
                manifest["assets"][-1]["url"] = (
                    "https://different.example/forged-attribution"
                )
                atomic_json(capture / "manifest.json", manifest)
                with self.assertRaises(Failure) as error:
                    acquire_source_evidence(fixture.request, provider, capture, seeds)
                self.assertEqual(error.exception.kind, "checkpoint_mismatch")
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(network.call_count, 1)


if __name__ == "__main__":
    unittest.main()
