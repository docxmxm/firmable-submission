import importlib.util
import csv
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "catalog.py"
spec = importlib.util.spec_from_file_location("catalog", SCRIPT)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def metadata(
    dataset_id,
    title="Company register",
    notes="Australian Business Number ABN and company name",
    formats=("CSV",),
):
    return {
        "id": dataset_id,
        "title": title,
        "notes": notes,
        "organization": {"title": "Public agency"},
        "resources": [{"format": value} for value in formats],
    }


class CatalogTests(unittest.TestCase):
    def test_uuid_dedup_preserves_portal_provenance_and_distinct_ids(self):
        shared = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        other = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        records = catalog.deduplicate(
            [
                ("national", metadata(shared)),
                ("qld", metadata(shared, formats=("XLSX",))),
                ("qld", metadata(other)),
            ]
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["portals"], ["national", "qld"])
        self.assertEqual(records[0]["formats"], ["CSV", "XLSX"])

    def test_harvested_original_uuid_deduplicates(self):
        original = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        harvested = metadata("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        harvested["extras"] = [{"key": "original_id", "value": original}]
        records = catalog.deduplicate(
            [("national", harvested), ("qld", metadata(original))]
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["aliases"]), 2)

    def test_non_uuid_ids_are_scoped_to_portal(self):
        records = catalog.deduplicate(
            [("national", metadata("register")), ("qld", metadata("register"))]
        )
        self.assertEqual(len(records), 2)

    def test_scoring_is_id_independent_and_penalizes_aggregate_titles(self):
        direct = catalog.normalize(metadata("one"), "national")
        other = catalog.normalize(metadata("different-id"), "national")
        self.assertEqual(
            catalog.score_record(direct)["score"], catalog.score_record(other)["score"]
        )
        aggregate = catalog.normalize(
            metadata("aggregate", title="Company register statistics"), "national"
        )
        self.assertEqual(
            catalog.score_record(aggregate)["score"],
            catalog.score_record(direct)["score"] - 4,
        )
        self.assertEqual(
            catalog.score_record(direct)["confidence_kind"],
            "uncalibrated_metadata_heuristic",
        )

    def test_unsupported_documents_do_not_outrank_tabular_business_records(self):
        scanned = catalog.normalize(
            metadata(
                "scan",
                title="Permit No. 39 survey by Oil Company",
                notes="Survey report",
                formats=("PDF",),
            ),
            "national",
        )
        tabular = catalog.normalize(
            metadata(
                "contracts",
                title="Contracts Register",
                notes="Official contract records",
                formats=("JSON",),
            ),
            "national",
        )
        self.assertLess(
            catalog.score_record(scanned)["score"],
            catalog.score_record(tabular)["score"],
        )

    def test_replay_ranking_and_unlabelled_random_audit_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            records = [
                catalog.normalize(metadata(f"record-{index:03d}"), "national")
                for index in range(220)
            ]
            catalog.write_jsonl(output / "catalog.jsonl", records)
            first = catalog.rank_snapshot(output, 200, 17)
            first_ranking = (output / "shortlist50.jsonl").read_bytes()
            first_audit = (output / "audit20.csv").read_bytes()
            second = catalog.rank_snapshot(output, 200, 17)
            self.assertEqual(first_ranking, (output / "shortlist50.jsonl").read_bytes())
            self.assertEqual(first_audit, (output / "audit20.csv").read_bytes())
            self.assertEqual(first["shortlist_sha256"], second["shortlist_sha256"])
            audit = catalog.read_jsonl(output / "audit20.jsonl")
            self.assertEqual(len({record["dataset_id"] for record in audit}), 20)
            self.assertTrue(
                all(
                    record["review_status"] == "pending_human_review"
                    for record in audit
                )
            )
            self.assertTrue(all(record["is_false_positive"] == "" for record in audit))
            self.assertIsNone(second["false_positive_rate"])

    def test_under_200_cannot_claim_completed_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            catalog.write_jsonl(
                output / "catalog.jsonl",
                [catalog.normalize(metadata("one"), "national")],
            )
            with self.assertRaisesRegex(ValueError, "Only 1 unique"):
                catalog.rank_snapshot(output, 200, 17)

    def test_replay_preserves_human_csv_labels_and_rejects_changed_audit_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            records = [
                catalog.normalize(metadata(f"record-{index:03d}"), "national")
                for index in range(220)
            ]
            catalog.write_jsonl(output / "catalog.jsonl", records)
            catalog.rank_snapshot(output, 200, 17)
            audit_path = output / "audit20.csv"
            with audit_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames
                rows = list(reader)
            rows[0]["reviewer"] = "Actual reviewer"
            rows[0]["is_false_positive"] = "false"
            catalog.write_csv(audit_path, rows, fields)
            reviewed_bytes = audit_path.read_bytes()
            manifest = catalog.rank_snapshot(output, 200, 17)
            self.assertEqual(audit_path.read_bytes(), reviewed_bytes)
            self.assertEqual(
                manifest["audit_status"], "human_review_present_not_scored"
            )
            with self.assertRaisesRegex(ValueError, "Existing human audit"):
                catalog.rank_snapshot(output, 200, 99)


if __name__ == "__main__":
    unittest.main()
