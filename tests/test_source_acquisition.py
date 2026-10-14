"""A source remains original evidence across bounded static asset navigation."""

import tempfile
import unittest
from pathlib import Path

from firmable_runtime.common import byte_hash, strict_json
from firmable_runtime.source_acquisition import SourceAcquisition, prepare_capture


class SourceAcquisitionTests(unittest.TestCase):
    def prepare(self, root, text, *, limits=None, media_type="text/html"):
        source = root / "publisher.source"
        source.write_text(text)
        directory = root / "capture"
        manifest = prepare_capture(
            directory,
            [
                {
                    "url": "https://publisher.example/catalogue",
                    "path": source,
                    "sha256": byte_hash(source.read_bytes()),
                    "media_type": media_type,
                }
            ],
            limits=limits,
        )
        return SourceAcquisition(directory), manifest["assets"][0]

    def test_full_html_preserves_embedded_metadata_outside_main(self):
        text = '<main>Visible catalogue</main>\n<script type="application/json">{"column":{"caption":"Operating title","valueKey":"MarketTitle"}}</script>\n<script src="/assets/entry.js"></script>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(Path(tmp), text)
            found = capture.search("MarketTitle")["matches"][0]
            self.assertEqual(found["text"], text[found["start"] : found["end"]])
            self.assertEqual(found["sha256"], byte_hash(text.encode()))
            self.assertIn('"caption":"Operating title"', found["text"])
            self.assertEqual(asset["embedded_blocks"][0]["kind"], "embedded_json")
            self.assertGreater(
                asset["embedded_blocks"][0]["start"], text.index("</main>")
            )
            self.assertIn(
                "static_capture_only",
                [o["kind"] for o in capture.inventory()["omissions"]],
            )

    def test_model_can_navigate_bundle_literals_to_component_with_lineage(self):
        seed = '<main>Catalogue</main><script src="/assets/entry.js"></script>'
        bundle = 'const chunks=["./unrelated.js","./company-cell.js"]; const components=[["CompanyTitleCell",chunks[1]]];'
        component = (
            'export function show(item){return "Operating as "+item["MarketTitle"]}'
        )
        calls = []

        def fetch(url, maximum):
            calls.append((url, maximum))
            body = bundle if url.endswith("entry.js") else component
            return {
                "url": url,
                "status": 200,
                "media_type": "text/javascript",
                "body": body.encode(),
            }

        with tempfile.TemporaryDirectory() as tmp:
            capture, seed_asset = self.prepare(Path(tmp), seed)
            capture.fetcher = fetch
            ref = next(
                r
                for r in capture.inventory()["references"]
                if r["url"].endswith("entry.js")
            )
            result = capture.fetch(ref["reference_id"])
            bundle_asset = result["asset"]
            self.assertEqual(bundle_asset["parent_sha256"], seed_asset["sha256"])
            self.assertEqual(bundle_asset["parent_reference_id"], ref["reference_id"])
            match = capture.search(
                "CompanyTitleCell", asset_id=bundle_asset["asset_id"]
            )["matches"][0]
            ref2 = next(
                r for r in match["references"] if r["url"].endswith("company-cell.js")
            )
            self.assertEqual(ref2["kind"], "literal_asset_reference")
            final = capture.fetch(ref2["reference_id"])["asset"]
            shown = capture.read(final["asset_id"])
            self.assertEqual(shown["text"], component)
            self.assertEqual(final["depth"], 2)
            self.assertEqual(final["parent_sha256"], bundle_asset["sha256"])
            self.assertEqual(capture.fetch(ref2["reference_id"])["status"], "reused")
            self.assertEqual(len(calls), 2)
            reopened = SourceAcquisition(capture.root)
            self.assertEqual(
                reopened.read(final["asset_id"])["sha256"], final["sha256"]
            )

    def test_unobserved_cross_origin_and_non_https_refs_cannot_fetch(self):
        text = '<script src="/local.js"></script><iframe src="https://other.example/remote"></iframe><a href="http://publisher.example/plain">other</a>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, _ = self.prepare(Path(tmp), text)
            calls = []
            capture.fetcher = lambda *args: calls.append(args)
            local = capture.manifest["references"][0]
            with self.assertRaisesRegex(ValueError, "exposed"):
                capture.fetch(local["reference_id"])
            inventory = capture.inventory()
            blocked = [r for r in inventory["references"] if not r["fetchable"]]
            self.assertEqual(
                {r["blocked_reason"] for r in blocked},
                {"cross_origin", "unsupported_url"},
            )
            for ref in blocked:
                with self.assertRaises(ValueError):
                    capture.fetch(ref["reference_id"])
            self.assertEqual(calls, [])

    def test_reference_and_original_hash_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(Path(tmp), '<script src="/good.js"></script>')
            ref = capture.inventory()["references"][0]
            ref["url"] = "https://publisher.example/unreferenced.js"
            with self.assertRaisesRegex(ValueError, "bound"):
                capture.fetch(ref["reference_id"])
            path = capture.root / asset["path"]
            path.write_text("changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                capture.read(asset["asset_id"])

    def test_external_manifest_asset_path_escape_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(Path(tmp), "original")
            original = capture.root / asset["path"]
            original.unlink()
            original.symlink_to(Path(tmp) / "publisher.source")
            with self.assertRaises(ValueError):
                SourceAcquisition(capture.root)
            asset["path"] = "../publisher.source"
            capture._save()
            with self.assertRaises(ValueError):
                SourceAcquisition(capture.root)

    def test_limits_and_failed_attempts_are_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, _ = self.prepare(
                Path(tmp),
                '<script src="/one.js"></script><script src="/two.js"></script>',
                limits={"max_fetches": 1, "max_asset_bytes": 200},
            )
            refs = capture.inventory()["references"]
            capture.fetcher = lambda url, limit: {
                "url": url,
                "status": 200,
                "media_type": "text/javascript",
                "body": b"x" * (limit + 1),
            }
            with self.assertRaisesRegex(ValueError, "limits"):
                capture.fetch(refs[0]["reference_id"])
            saved = strict_json((capture.root / "manifest.json").read_bytes())
            self.assertEqual(saved["attempts"][0]["status"], "failed")
            self.assertEqual(len(saved["assets"]), 1)
            capture = SourceAcquisition(capture.root)
            with self.assertRaisesRegex(ValueError, "already"):
                capture.fetch(refs[0]["reference_id"])
            with self.assertRaisesRegex(ValueError, "budget"):
                capture.fetch(refs[1]["reference_id"])

    def test_cross_origin_redirect_and_pdf_response_fail_without_artifact(self):
        for destination, media in [
            ("https://other.example/a.js", "text/javascript"),
            ("https://publisher.example/a.js", "application/pdf"),
        ]:
            with (
                self.subTest(destination=destination, media=media),
                tempfile.TemporaryDirectory() as tmp,
            ):
                capture, _ = self.prepare(Path(tmp), '<script src="/a.js"></script>')
                capture.fetcher = lambda url, maximum: {
                    "url": destination,
                    "status": 200,
                    "media_type": media,
                    "body": b"data",
                }
                ref = capture.inventory()["references"][0]
                with self.assertRaises(ValueError):
                    capture.fetch(ref["reference_id"])
                self.assertEqual(len(capture.manifest["assets"]), 1)

    def test_depth_and_total_stored_bytes_are_bounded(self):
        text = '<script src="/a.js"></script>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, _ = self.prepare(
                Path(tmp),
                text,
                limits={"max_depth": 1, "max_total_bytes": len(text.encode()) + 20},
            )
            capture.fetcher = lambda url, maximum: {
                "url": url,
                "status": 200,
                "media_type": "text/javascript",
                "body": b'let x="./next.js";',
            }
            ref = capture.inventory()["references"][0]
            child = capture.fetch(ref["reference_id"])["asset"]
            child_ref = capture.read(child["asset_id"])["references"][0]
            self.assertEqual(child_ref["blocked_reason"], "depth_limit")
            with self.assertRaises(ValueError):
                capture.fetch(child_ref["reference_id"])
            self.assertLessEqual(
                capture.inventory()["usage"]["bytes"], capture.limits["max_total_bytes"]
            )

    def test_exact_unicode_excerpts_literal_search_and_explicit_truncation(self):
        text = "\u5b57\n" + "Field.* literal\n" * 20
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(Path(tmp), text, media_type="text/plain")
            result = capture.search("Field.*", max_matches=2)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["omissions"][0]["omitted"], 18)
            self.assertEqual(capture.search("Field.+")["matches"], [])
            part = capture.read(asset["asset_id"], start=1, max_chars=15)
            self.assertEqual(part["text"], text[1:16])
            self.assertEqual(part["text_sha256"], byte_hash(text[1:16].encode()))
            self.assertTrue(part["truncated"])
            for kwargs in ({"start": -1}, {"start": True}, {"max_chars": 20000}):
                with self.assertRaises(ValueError):
                    capture.read(asset["asset_id"], **kwargs)

    def test_reference_index_limit_is_disclosed_and_original_remains_searchable(self):
        text = '<a href="/first">First</a><script src="/last.js"></script>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(
                Path(tmp), text, limits={"max_references_per_asset": 1}
            )
            self.assertEqual(len(capture.inventory()["references"]), 1)
            self.assertEqual(asset["omissions"][0]["kind"], "reference_index_limit")
            self.assertIn("last.js", capture.search("last.js")["matches"][0]["text"])

    def test_seed_hash_budget_and_capture_overwrite_are_checked_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture, _ = self.prepare(root, "original")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                prepare_capture(capture.root, [])
            source = root / "publisher.source"
            bad = {
                "url": "https://publisher.example/a",
                "path": source,
                "sha256": "0" * 64,
            }
            with self.assertRaisesRegex(ValueError, "hash"):
                prepare_capture(root / "bad", [bad])
            self.assertFalse((root / "bad").exists())

    def test_duplicate_url_cannot_retry_failed_or_unknown_fetch_after_resume(self):
        for status in ("failed", "started"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                capture, _ = self.prepare(
                    Path(tmp),
                    '<script src="/app.js"></script><a href="/app.js">same</a>',
                )
                refs = [
                    r
                    for r in capture.inventory()["references"]
                    if r["url"].endswith("/app.js")
                ]
                calls = []

                def fail(url, maximum):
                    calls.append(url)
                    raise TimeoutError("interrupted response")

                capture.fetcher = fail
                with self.assertRaises(ValueError):
                    capture.fetch(refs[0]["reference_id"])
                capture.manifest["attempts"][0]["status"] = status
                capture._save()
                resumed = SourceAcquisition(capture.root, fetcher=fail)
                with self.assertRaisesRegex(ValueError, "URL already"):
                    resumed.fetch(refs[1]["reference_id"])
                self.assertEqual(len(calls), 1)

    def test_redirected_asset_is_reused_even_if_completion_receipt_was_interrupted(
        self,
    ):
        for status in ("captured", "started"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                capture, _ = self.prepare(
                    Path(tmp),
                    '<script src="/app.js"></script><a href="/app.js">same</a>',
                )
                refs = [
                    r
                    for r in capture.inventory()["references"]
                    if r["url"].endswith("/app.js")
                ]
                capture.fetcher = lambda url, bound: {
                    "url": "https://publisher.example/versioned/app.js",
                    "status": 200,
                    "media_type": "text/javascript",
                    "body": b"original component",
                }
                asset = capture.fetch(refs[0]["reference_id"])["asset"]
                if status == "started":
                    attempt = capture.manifest["attempts"][0]
                    for key in (
                        "asset_id",
                        "final_url",
                        "sha256",
                        "bytes",
                        "completed_utc",
                        "response_status",
                    ):
                        attempt.pop(key, None)
                    attempt["status"] = "started"
                    capture._save()

                def forbidden(*args):
                    self.fail("A known captured response must not be fetched again")

                resumed = SourceAcquisition(capture.root, fetcher=forbidden)
                result = resumed.fetch(refs[1]["reference_id"])
                self.assertEqual(result["status"], "reused")
                self.assertEqual(result["asset"]["asset_id"], asset["asset_id"])
                self.assertIn(
                    resumed.manifest["attempts"][0]["status"],
                    {"captured", "captured_recovered"},
                )

    def test_first_base_resolves_relative_references_and_retains_origin_boundary(self):
        cases = [
            (
                '<base href="/static/"><base href="/ignored/">',
                "https://publisher.example/static/app.js",
                None,
            ),
            (
                '<base href="https://outside.example/static/">',
                "https://outside.example/static/app.js",
                "cross_origin",
            ),
            (
                '<base href="http://publisher.example/static/">',
                "http://publisher.example/static/app.js",
                "unsupported_url",
            ),
        ]
        for prefix, expected, blocked in cases:
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                capture, asset = self.prepare(
                    Path(tmp), prefix + '<script src="app.js"></script>'
                )
                ref = next(
                    r
                    for r in capture.inventory()["references"]
                    if r["kind"] == "html_script_src"
                )
                self.assertEqual(ref["url"], expected)
                self.assertEqual(ref["blocked_reason"], blocked)
                self.assertTrue(
                    any(b["kind"] == "html_base" for b in asset["embedded_blocks"])
                )

    def test_malformed_reference_is_reported_without_losing_valid_source(self):
        text = '<a href="https://[">broken</a><script src="/valid.js"></script>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.prepare(Path(tmp), text)
            inventory = capture.inventory()
            self.assertTrue(
                any(
                    r["url"].endswith("/valid.js") and r["fetchable"]
                    for r in inventory["references"]
                )
            )
            self.assertTrue(
                any(
                    r["blocked_reason"] == "unsupported_url"
                    for r in inventory["references"]
                )
            )
            self.assertIn(
                "unsupported_reference_url", [o["kind"] for o in asset["omissions"]]
            )
            self.assertEqual(capture.read(asset["asset_id"])["text"], text)


if __name__ == "__main__":
    unittest.main()
