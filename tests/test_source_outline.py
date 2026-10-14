"""Source prose remains findable when it does not repeat a native header."""

import tempfile
import unittest
from pathlib import Path

from firmable_runtime.common import byte_hash
from firmable_runtime.source_acquisition import SourceAcquisition, prepare_capture


class SourceOutlineTests(unittest.TestCase):
    def capture(self, root, text):
        source = root / "help.html"
        source.write_text(text)
        path = root / "capture"
        prepare_capture(
            path,
            [
                {
                    "url": "https://publisher.example/help",
                    "path": source,
                    "sha256": byte_hash(source.read_bytes()),
                    "media_type": "text/html",
                }
            ],
        )
        capture = SourceAcquisition(path)
        return capture, capture.inventory()["assets"][0]

    def test_original_heading_offsets_find_definition_after_a_large_script(self):
        text = "<script>" + "var unrelated=1;" * 1500 + "</script>\n"
        text += "<h2>Register column meanings</h2><p>The second column names the licensed business.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.capture(Path(tmp), text)
            self.assertEqual(capture.search("OpaqueColumn42")["matches"], [])
            section = next(
                b for b in asset["embedded_blocks"] if b["kind"] == "html_h2"
            )
            self.assertGreater(section["start"], 16000)
            read = capture.read(asset["asset_id"], section["start"], 1000)
            self.assertIn(
                "The second column names the licensed business.", read["text"]
            )
            self.assertEqual(read["text"], text[read["start"] : read["end"]])
            self.assertEqual(read["sha256"], byte_hash(text.encode()))

    def test_outline_keeps_unicode_and_links_as_original_navigation_text(self):
        text = '<title>Résumé &amp; register</title><main><a href="/fields">Field meanings</a><p>原始说明</p></main>'
        with tempfile.TemporaryDirectory() as tmp:
            capture, asset = self.capture(Path(tmp), text)
            blocks = asset["embedded_blocks"]
            self.assertEqual(
                next(b for b in blocks if b["kind"] == "html_title")["source_preview"],
                "Résumé &amp; register",
            )
            link = next(b for b in blocks if b["kind"] == "html_a")
            self.assertEqual(link["source_preview"], "Field meanings")
            excerpt = capture.read(asset["asset_id"], link["start"], 100)
            self.assertEqual(
                excerpt["references"][0]["url"], "https://publisher.example/fields"
            )
            self.assertIn("原始说明", excerpt["text"])

    def test_outline_is_bounded_and_retains_headings_before_navigation_noise(self):
        text = "".join(f'<a href="/{i}">Navigation {i}</a>' for i in range(90))
        text += "<h1>Source field definitions</h1><p>Original body</p>"
        with tempfile.TemporaryDirectory() as tmp:
            _, asset = self.capture(Path(tmp), text)
            self.assertLessEqual(len(asset["embedded_blocks"]), 32)
            self.assertEqual(asset["embedded_blocks"][0]["kind"], "html_h1")
            self.assertTrue(
                any(o["kind"] == "document_outline_limit" for o in asset["omissions"])
            )


if __name__ == "__main__":
    unittest.main()
