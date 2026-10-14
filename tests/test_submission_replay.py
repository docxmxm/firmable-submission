"""Check that the published snapshot can be reproduced without model calls."""

import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.common import byte_hash, canonical
from firmable_runtime.execution import discover, run_engine
from firmable_runtime.llm_route import is_llm
from firmable_runtime.workflow import build_outputs, withdraw_outputs
from scripts.score_review import score_review


PROJECT = Path(__file__).resolve().parents[1]


class SubmissionReplayTests(unittest.TestCase):
    def test_configs_reproduce_the_submitted_observation_bytes(self):
        for path in sorted((PROJECT / "outputs/configs").glob("*.json")):
            with self.subTest(source=path.stem):
                config = json.loads(path.read_bytes())
                self.assertFalse(is_llm(config), "Snapshot replay must make no model calls")
                saved = PROJECT / "outputs/observations" / (path.stem + ".jsonl")
                saved_bytes = saved.read_bytes()
                observations = [json.loads(line) for line in saved_bytes.splitlines()]
                timestamps = {record["ingested_at"] for record in observations}
                self.assertEqual(len(timestamps), 1, "One frozen snapshot per source")
                evidence = PROJECT / "data/assessment-inputs" / path.stem / "evidence"
                tables, _, _, documentation = discover(evidence, path.stem)
                records = [
                    record
                    for index in config["source_schema"]["selected_table_ids"]
                    for record in tables[index]["records"]
                ]
                metadata = json.loads(
                    (evidence.parent / "catalogue_metadata.json").read_bytes()
                )
                replayed, report = run_engine(
                    config, records, documentation, metadata, timestamps.pop()
                )
                self.assertEqual(report["errors"], [])
                payload = b"".join(canonical(record) + b"\n" for record in replayed)
                self.assertEqual(byte_hash(payload), byte_hash(saved_bytes))

    def test_rebuild_reproduces_links_profiles_queue_and_withdrawal(self):
        paths = sorted((PROJECT / "outputs/observations").glob("*.jsonl"))
        withdrawal = json.loads(
            (PROJECT / "outputs/withdrawal/withdrawal.json").read_bytes()
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            build_outputs(paths, destination)
            withdraw_outputs(
                paths, withdrawal["withdraw_source_id"], destination / "withdrawal"
            )
            for path in destination.rglob("*.json"):
                with self.subTest(artifact=str(path.relative_to(destination))):
                    published = PROJECT / "outputs" / path.relative_to(destination)
                    self.assertEqual(byte_hash(path.read_bytes()), byte_hash(published.read_bytes()))

    def test_human_audit_is_complete_and_bound_to_the_published_population(self):
        review_dir = PROJECT / "outputs/human-review"
        scored = score_review(review_dir)
        saved = json.loads((review_dir / "summary.json").read_bytes())
        self.assertEqual(scored["human_reviewed"], 50)
        self.assertEqual(scored["review_status"], "complete")
        for key in ("decision_counts", "precision", "reviews_sha256", "samples_sha256"):
            self.assertEqual(scored[key], saved[key])
        receipt = json.loads((review_dir / "CONFIRMATION-RECEIPT.json").read_bytes())
        self.assertEqual(receipt["ai_judgments_included"], 0)
        self.assertEqual(receipt["reviews_sha256"], scored["reviews_sha256"])
        self.assertEqual(receipt["final_links_sha256"], scored["links_sha256"])


if __name__ == "__main__":
    unittest.main()
