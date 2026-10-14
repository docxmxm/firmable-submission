"""Replay the selected pre-scope r2 cohort; fidelity is not semantic accuracy."""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from firmable_runtime.common import byte_hash, canonical, digest
from firmable_runtime.config_audit import validated_name_review
from firmable_runtime.execution import discover, run_engine
from firmable_runtime.workflow import build_outputs

PROJECT = Path(__file__).resolve().parents[1]


def informative_values(observation):
    return {
        group + "." + field: value
        for group in ("entity", "address")
        for field, value in observation.get(group, {}).items()
        if value not in (None, "", "unknown")
    }


class AssessmentReplayTests(unittest.TestCase):
    def test_selected_r2_configs_reproduce_native_identity_and_informative_values(self):
        manifest = json.loads(
            (PROJECT / "evidence/selected-r2/manifest.json").read_text()
        )
        sources = sorted((PROJECT / "data/assessment-inputs").glob("real-*"))
        self.assertEqual(len(sources), 6)
        self.assertEqual(set(manifest["sources"]), {source.name for source in sources})
        self.assertIs(manifest["human_gold"], False)
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for source in sources:
                with self.subTest(source=source.name):
                    binding = manifest["sources"][source.name]
                    config_path = PROJECT / "outputs/configs" / (source.name + ".json")
                    self.assertEqual(config_path, PROJECT / binding["config_path"])
                    config_bytes = config_path.read_bytes()
                    self.assertEqual(byte_hash(config_bytes), binding["config_sha256"])
                    config = json.loads(config_bytes)
                    self.assertEqual(digest(config), binding["config_canonical_sha256"])
                    saved_bytes = (PROJECT / binding["observations_path"]).read_bytes()
                    self.assertEqual(
                        byte_hash(saved_bytes), binding["observations_sha256"]
                    )
                    saved = [json.loads(line) for line in saved_bytes.splitlines()]
                    self.assertEqual(len(saved), binding["rows"])
                    saved_by_id = {row["source_record_id"]: row for row in saved}
                    self.assertEqual(len(saved_by_id), len(saved))

                    metadata = json.loads(
                        (source / "catalogue_metadata.json").read_text()
                    )
                    tables, _, _, docs = discover(source / "evidence", source.name)
                    rows = [
                        row
                        for i in config["source_schema"]["selected_table_ids"]
                        for row in tables[i]["records"]
                    ]
                    self.assertEqual(len(rows), len(saved))
                    self.assertEqual(
                        {row["source_record_id"] for row in rows}, set(saved_by_id)
                    )
                    name_indices = {
                        i
                        for i, mapping in enumerate(config["field_mappings"])
                        if mapping["canonical_field"]
                        in {"entity.legal_name", "entity.trading_name"}
                    }
                    audit_path = binding["selected_audit_path"]
                    if audit_path:
                        audit_bytes = (PROJECT / audit_path).read_bytes()
                        self.assertEqual(
                            byte_hash(audit_bytes), binding["selected_audit_sha256"]
                        )
                        audit = json.loads(audit_bytes)
                        self.assertEqual(audit["candidate_config_hash"], digest(config))
                        self.assertEqual(audit["documentation_hash"], digest(docs))
                        self.assertEqual(audit["errors"], [])
                        self.assertIs(audit["human_gold"], False)
                        self.assertEqual(audit["name_mapping_count"], len(name_indices))
                        self.assertEqual(
                            audit["status"],
                            "supported" if name_indices else "no_name_mapping",
                        )
                        self.assertEqual(
                            {review["mapping_index"] for review in audit["reviews"]},
                            name_indices,
                        )
                        for review in audit["reviews"]:
                            self.assertEqual(review["verdict"], "supported")
                            self.assertIs(review["evidence_verified"], True)
                            self.assertIs(review["branch_analysis_verified"], True)
                            mapping = config["field_mappings"][review["mapping_index"]]
                            for part in [review, *review["branch_reviews"]]:
                                for reference in part["evidence_quotes"]:
                                    self.assertIn(
                                        reference["quote"],
                                        docs
                                        if reference["kind"] == "documentation"
                                        else mapping["source_fields"],
                                    )
                    else:
                        self.assertFalse(name_indices, "Selected name audit is missing")

                    # An ordinary audit may use sampled companion evidence.
                    # Only bound overrides require the stricter static proof.
                    bound_path = binding["bound_name_review_path"]
                    bound_review = (
                        json.loads((PROJECT / bound_path).read_text())
                        if bound_path
                        else None
                    )
                    if bound_review is not None:
                        self.assertEqual(
                            validated_name_review(config, docs, bound_review),
                            name_indices,
                        )
                    observations, report = run_engine(
                        config,
                        rows,
                        docs,
                        metadata,
                        "2026-10-14T00:00:00+00:00",
                        name_review=bound_review,
                    )
                    self.assertEqual(report["errors"], [])
                    self.assertEqual(len(observations), len(saved))
                    self.assertEqual(
                        [row["source_record_id"] for row in observations],
                        [row["source_record_id"] for row in saved],
                    )
                    for row in observations:
                        original = saved_by_id[row["source_record_id"]]
                        for field in ("source_id", "native_locator", "raw_record"):
                            self.assertEqual(row[field], original[field])
                        self.assertEqual(
                            informative_values(row),
                            informative_values(original),
                            row["source_record_id"],
                        )
                    self.assertEqual(
                        Counter(
                            field
                            for row in observations
                            for field in informative_values(row)
                        ),
                        Counter(
                            field for row in saved for field in informative_values(row)
                        ),
                    )
                    path = Path(directory) / (source.name + ".jsonl")
                    path.write_bytes(
                        b"".join(canonical(row) + b"\n" for row in observations)
                    )
                    paths.append(path)
            result = build_outputs(paths, Path(directory) / "build")
            expected = json.loads((PROJECT / "outputs/summary.json").read_text())
            for field in (
                "observation_count",
                "link_count",
                "unlinked_count",
                "profile_population",
                "profile_count",
                "threshold",
            ):
                self.assertEqual(result[field], expected[field], field)
