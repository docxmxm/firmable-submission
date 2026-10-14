"""Original document page retrieval stays bounded, reproducible and read-only."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from firmable_runtime.common import Failure, byte_hash, canonical
from firmable_runtime.execution import discover
from firmable_runtime.field_evidence import inspect_field_evidence
from firmable_runtime.source_document_tools import (
    MAX_CONTEXT_BYTES,
    document_pages,
    inspect_document_context,
)


def make_pdf(path, texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 40 750 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def manifest(path):
    return [{"path": path.name, "sha256": byte_hash(path.read_bytes())}]


class SourceDocumentToolTests(unittest.TestCase):
    def test_markdown_only_field_lookup_preserves_adjacent_section_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.md"
            path.write_text("# Preface\fTerm: Business name\fFollowing condition")
            runtime = SimpleNamespace(
                docs=path.read_text(),
                proposal_records=lambda: [],
                evidence_manifest=manifest(path),
                input={"evidence_root": root},
            )
            result = inspect_field_evidence(
                runtime, {"fields": [{"source_field": "Term"}]}, "Term"
            )
            pages = result["original_document_pages"]["pages"]
            self.assertEqual([p["page_number"] for p in pages], [2, 3, 1])
            self.assertEqual(pages[0]["file"], "publisher.md")
            self.assertEqual(pages[0]["file_sha256"], byte_hash(path.read_bytes()))
            self.assertEqual(pages[1]["text"], "Following condition")

    def test_markdown_rendition_prevents_duplicate_pdf_prompt_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_pdf(root / "publisher.pdf", ["Original PDF repeated material"])
            (root / "publisher.md").write_text("# Prepared complete rendition")
            (root / "data.csv").write_text("Term\nExample\n")
            _, _, inputs, docs = discover(root, "generic-source")
            self.assertEqual(docs, "# Prepared complete rendition")
            self.assertIn("publisher.pdf", [item["path"] for item in inputs])

    def test_text_complete_pages_and_adjacent_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.txt"
            path.write_text(
                "Introduction\fCurrent Term definition\fIF alternate condition applies\f"
            )
            result = inspect_document_context(root, manifest(path), "Current Term")
            self.assertEqual([p["page_number"] for p in result["pages"]], [2, 3, 1])
            self.assertEqual(
                result["pages"][1]["text"], "IF alternate condition applies"
            )
            self.assertEqual(
                result["pages"][0]["file_sha256"], byte_hash(path.read_bytes())
            )
            self.assertFalse(result["truncated"])
            self.assertEqual(result["documents"][0]["page_count"], 3)

    def test_pdf_layout_and_one_based_physical_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.pdf"
            make_pdf(path, ["Cover", "Original Publisher Term", "Following condition"])
            pages = document_pages(root, manifest(path))
            result = inspect_document_context(root, manifest(path), "Publisher Term")
            self.assertEqual(result["pages"][0]["page_number"], 2)
            self.assertEqual(result["pages"][0]["extraction"], "pypdf_layout")
            self.assertEqual(result["pages"][0]["text"], pages["pages"][1]["text"])
            self.assertEqual(
                result["pages"][0]["text_sha256"],
                byte_hash(pages["pages"][1]["text"].encode()),
            )
            self.assertEqual(result["pages"][1]["page_number"], 3)

    def test_ranks_occurrence_count_and_reports_all_hit_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.txt"
            path.write_text(
                "Term in field list\fTerm Term Term definition\fContinuation\fTerm appendix"
            )
            result = inspect_document_context(root, manifest(path), "Term")
            self.assertEqual([p["page_number"] for p in result["pages"]], [2, 3, 1])
            self.assertEqual(
                [p["page_number"] for p in result["matching_pages"]], [2, 1, 4]
            )
            self.assertTrue(result["truncated"])
            self.assertEqual(result["omitted_page_count"], 1)

    def test_original_pdf_wins_tie_over_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf, txt = root / "original.pdf", root / "earlier.txt"
            make_pdf(pdf, ["Term definition"])
            txt.write_text("Term definition")
            result = inspect_document_context(
                root, manifest(txt) + manifest(pdf), "Term", 1
            )
            self.assertEqual(result["pages"][0]["file"], "original.pdf")
            self.assertTrue(result["truncated"])

    def test_oversize_page_omitted_completely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.md"
            path.write_text(
                "Term " + "x" * MAX_CONTEXT_BYTES + "\fFollowing complete page"
            )
            result = inspect_document_context(root, manifest(path), "Term")
            self.assertEqual(
                [p["text"] for p in result["pages"]], ["Following complete page"]
            )
            self.assertEqual(
                result["omitted_pages"][0]["reason"], "oversize_complete_page"
            )
            self.assertEqual(result["omitted_page_count"], 1)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(canonical(result)), MAX_CONTEXT_BYTES)

    def test_total_bound_counts_utf8_and_full_result_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.txt"
            text = "Term " + "字" * 5000
            path.write_text(text + "\f" + text)
            result = inspect_document_context(root, manifest(path), "Term")
            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual(result["pages"][0]["text"], text)
            self.assertEqual(result["omitted_page_count"], 1)
            self.assertLessEqual(len(canonical(result)), MAX_CONTEXT_BYTES)

    def test_blank_pdf_explicitly_requires_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "scan.pdf"
            make_pdf(path, [None])
            pages = document_pages(root, manifest(path))
            self.assertTrue(pages["pages"][0]["ocr_required"])
            result = inspect_document_context(root, manifest(path), "Term")
            self.assertEqual(result["pages"], [])
            self.assertEqual(result["documents"][0]["ocr_required_pages"], [1])

    def test_manifest_hash_path_and_symlink_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.txt"
            path.write_text("Term")
            with self.assertRaises(Failure):
                document_pages(root, [{"path": "../publisher.txt", "sha256": "0" * 64}])
            with self.assertRaises(Failure):
                document_pages(root, [{"path": path.name, "sha256": "0" * 64}])
            link = root / "link.txt"
            link.symlink_to(path)
            with self.assertRaises(Failure):
                document_pages(
                    root, [{"path": link.name, "sha256": byte_hash(path.read_bytes())}]
                )

    def test_no_native_rows_are_read_and_queries_are_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "publisher.txt"
            path.write_text("Code.* is a literal field")
            # Nonexistent native files are never opened by this document tool.
            inputs = manifest(path) + [
                {"path": "data.csv", "sha256": "0" * 64},
                {"path": "data.json", "sha256": "0" * 64},
            ]
            result = inspect_document_context(root, inputs, "Code.*")
            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual(len(result["documents"]), 1)
            self.assertEqual(
                inspect_document_context(root, inputs, "Code.+")["pages"], []
            )
            for query, count in (("", 3), ("Term", 4), ("Term", True)):
                with self.assertRaises(Failure):
                    inspect_document_context(root, inputs, query, count)


if __name__ == "__main__":
    unittest.main()
