"""Literal source URLs retain their original context through the planner."""

import copy
import tempfile
import unittest

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import digest
from firmable_runtime.execution import pointer_get
from firmable_runtime.source_document_tools import document_pages, source_url_candidates
from test_agent import NativeFixture, ScriptedProvider
from test_source_document_tools import manifest, make_pdf


class SourceUrlCandidateTests(unittest.TestCase):
    def test_metadata_paths_and_exact_occurrences_without_named_key_rules(self):
        metadata = {
            "unusual/~key": [
                {
                    "value": "See https://alpha.example/manual.pdf#section-2 for details."
                },
                "http://beta.example/reference?q=1&lang=en",
            ],
            "different": "https://alpha.example/manual.pdf#section-2",
            "description": '<a href="https://gamma.example/guide">Original link</a>',
            "relative": "../not-an-absolute-url.pdf",
        }
        original = copy.deepcopy(metadata)
        candidates = source_url_candidates(metadata)
        self.assertEqual(len(candidates), 4)
        self.assertEqual(metadata, original)
        self.assertEqual(candidates, source_url_candidates(metadata))
        self.assertEqual(
            len([c for c in candidates if c["url"].startswith("https://alpha")]), 2
        )
        self.assertIn(
            "/unusual~1~0key/0/value",
            {c["source_ref"]["json_pointer"] for c in candidates},
        )
        for candidate in candidates:
            ref = candidate["source_ref"]
            self.assertEqual(ref["kind"], "catalogue_metadata")
            self.assertEqual(ref["metadata_sha256"], digest(metadata))
            source_text = pointer_get(metadata, ref["json_pointer"])
            self.assertEqual(source_text[ref["start"] : ref["end"]], candidate["url"])
            self.assertNotIn("supported", candidate)
            self.assertNotIn("trusted", candidate)

    def test_original_page_references_keep_file_page_hash_and_text_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = NativeFixture(tmp)
            pdf = fixture.evidence / "publisher.pdf"
            make_pdf(
                pdf,
                ["Cover", "See https://docs.example/guide.pdf for more information."],
            )
            pages = document_pages(fixture.evidence, manifest(pdf))["pages"]
            candidates = source_url_candidates({}, pages=pages)
            self.assertEqual(len(candidates), 1)
            ref = candidates[0]["source_ref"]
            self.assertEqual(ref["kind"], "original_document")
            self.assertEqual(ref["page_number"], 2)
            for field in ("file", "file_sha256", "text_sha256", "extraction"):
                self.assertEqual(ref[field], pages[1][field])
            self.assertEqual(
                pages[1]["text"][ref["start"] : ref["end"]], candidates[0]["url"]
            )

    def test_text_delimiters_do_not_become_urls_and_balanced_path_is_retained(self):
        candidates = source_url_candidates(
            {
                "text": "Read [a](https://docs.example/a.pdf). Then https://docs.example/a_(b).pdf, or <https://other.example/x>."
            }
        )
        self.assertEqual(
            [c["url"] for c in candidates],
            [
                "https://docs.example/a.pdf",
                "https://docs.example/a_(b).pdf",
                "https://other.example/x",
            ],
        )
        self.assertEqual(
            source_url_candidates({"none": [None, 10, False], "text": "No URL here."}),
            [],
        )

    def test_full_metadata_and_navigation_refs_reach_planner_and_candidate_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = NativeFixture(tmp)
            metadata = {
                "licence": "CC BY 4.0",
                "nested": {"unusual": ["https://publisher.example/definitions"]},
            }
            fixture.request["catalogue_metadata"] = metadata
            path = fixture.evidence / "definitions.md"
            path.write_text(
                path.read_text() + "\nSource: https://publisher.example/catalogue\n"
            )
            provider = ScriptedProvider(fixture, ["good", "select"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "complete")
            contexts = [
                r["payload"]
                for r in provider.requests
                if r["purpose"]
                in {"agent_decision", "candidate_manifest", "candidate_rules"}
            ]
            self.assertGreaterEqual(len(contexts), 4)
            for context in contexts:
                self.assertEqual(context["catalogue_metadata"], metadata)
                self.assertEqual(
                    {c["url"] for c in context["source_url_candidates"]},
                    {
                        "https://publisher.example/definitions",
                        "https://publisher.example/catalogue",
                    },
                )
                self.assertEqual(
                    {c["source_ref"]["kind"] for c in context["source_url_candidates"]},
                    {"catalogue_metadata", "original_document"},
                )
            self.assertTrue(result["approval_required"])
            self.assertIsNone(result["approval"])
            calls = len(provider.requests)
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), calls)


if __name__ == "__main__":
    unittest.main()
