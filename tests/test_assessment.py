"""Preserve r2 claims; explicitly source-bound additions are not a fresh cohort."""

import json
import tarfile
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from firmable_runtime.common import byte_hash, canonical, digest
from firmable_runtime.config_audit import validated_name_review
from firmable_runtime.execution import discover, pointer_get, run_engine
from firmable_runtime.native import field_value
from firmable_runtime.workflow import build_outputs

PROJECT = Path(__file__).resolve().parents[1]


def historical_output(archive, path):
    """Read retained r2 bytes without confusing them with the current outputs."""
    if not path.startswith("outputs/"):
        raise AssertionError("Historical output path must retain its logical prefix")
    member = archive.extractfile("historical-r2/" + path)
    if member is None:
        raise AssertionError("Missing historical output: " + path)
    return member.read()


def informative_values(observation):
    return {
        group + "." + field: value
        for group in ("entity", "address")
        for field, value in observation.get(group, {}).items()
        if value not in (None, "", "unknown")
    }


def explicit_location_spellings(values):
    """Independent whole-source spellings, without invoking the runtime parser."""
    state_names = {
        "NSW": "NEW SOUTH WALES",
        "VIC": "VICTORIA",
        "QLD": "QUEENSLAND",
        "WA": "WESTERN AUSTRALIA",
        "SA": "SOUTH AUSTRALIA",
        "TAS": "TASMANIA",
        "ACT": "AUSTRALIAN CAPITAL TERRITORY",
        "NT": "NORTHERN TERRITORY",
    }
    state = values["address.state"]
    return {
        separator.join(
            (values["address.locality"], spelling, values["address.postcode"])
        )
        + suffix
        for spelling in (state, state_names[state])
        for separator in (",", " ")
        for suffix in ("", separator + "AU", separator + "AUSTRALIA")
    }


class AssessmentReplayTests(unittest.TestCase):
    def test_selected_r2_claims_preserved_and_additions_match_explicit_native_text(self):
        manifest = json.loads(
            (PROJECT / "evidence/selected-r2/manifest.json").read_text()
        )
        sources = sorted((PROJECT / "data/assessment-inputs").glob("real-*"))
        self.assertEqual(len(sources), 6)
        self.assertEqual(set(manifest["sources"]), {source.name for source in sources})
        self.assertIs(manifest["human_gold"], False)
        with (
            tempfile.TemporaryDirectory() as directory,
            tarfile.open(
                PROJECT / manifest["storage_mapping"]["archive"], "r:gz"
            ) as archive,
        ):
            paths = []
            for source in sources:
                with self.subTest(source=source.name):
                    binding = manifest["sources"][source.name]
                    config_bytes = historical_output(archive, binding["config_path"])
                    self.assertEqual(byte_hash(config_bytes), binding["config_sha256"])
                    config = json.loads(config_bytes)
                    self.assertEqual(digest(config), binding["config_canonical_sha256"])
                    saved_bytes = historical_output(
                        archive, binding["observations_path"]
                    )
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
                    added_counts = Counter()
                    for row in observations:
                        original = saved_by_id[row["source_record_id"]]
                        for field in ("source_id", "native_locator", "raw_record"):
                            self.assertEqual(row[field], original[field])
                        actual = informative_values(row)
                        prior = informative_values(original)
                        self.assertEqual(
                            {field: actual.get(field) for field in prior},
                            prior,
                            row["source_record_id"],
                        )
                        added = actual.keys() - prior.keys()
                        if added:
                            self.assertEqual(
                                added,
                                {"address.locality", "address.state", "address.postcode"},
                            )
                            inputs = set()
                            for mapping in config["field_mappings"]:
                                target = mapping["canonical_field"]
                                if target not in added:
                                    continue
                                expression = pointer_get(
                                    config, mapping["execution_pointer"]
                                )
                                self.assertEqual(expression["op"], "parse_au_location")
                                self.assertEqual(
                                    target, "address." + expression["component"]
                                )
                                self.assertEqual(expression["input"]["op"], "field")
                                inputs.add(expression["input"]["name"])
                            self.assertEqual(len(inputs), 1)
                            raw = field_value(row["raw_record"], next(iter(inputs)))
                            self.assertIsInstance(raw, str)
                            normalized = ",".join(
                                " ".join(p.split()) for p in raw.split(",")
                            )
                            self.assertIn(
                                normalized.upper(),
                                {s.upper() for s in explicit_location_spellings(actual)},
                                row["source_record_id"],
                            )
                            added_counts.update(added)
                    self.assertEqual(
                        Counter(
                            field
                            for row in observations
                            for field in informative_values(row)
                        ),
                        Counter(
                            field for row in saved for field in informative_values(row)
                        ) + added_counts,
                    )
                    path = Path(directory) / (source.name + ".jsonl")
                    path.write_bytes(
                        b"".join(canonical(row) + b"\n" for row in observations)
                    )
                    paths.append(path)
            result = build_outputs(paths, Path(directory) / "build")
            summary_bytes = historical_output(archive, "outputs/summary.json")
            self.assertEqual(
                byte_hash(summary_bytes), manifest["storage_mapping"]["summary_sha256"]
            )
            expected = json.loads(summary_bytes)
            for field in (
                "observation_count",
                "profile_count",
                "threshold",
            ):
                self.assertEqual(result[field], expected[field], field)
            # History stays frozen. A repaired role contract may admit additional
            # identifier-backed pairs, but must preserve every old pair and prove
            # the reason for each addition rather than merely accept a new count.
            previous = json.loads(historical_output(archive, "outputs/links.json"))
            current = json.loads((Path(directory) / "build/links.json").read_text())

            def pair_key(pair):
                return tuple(sorted((tuple(pair["left"]), tuple(pair["right"]))))

            def pair_map(graph):
                return {
                    pair_key(pair): pair
                    for group in graph["links"]
                    for pair in group["pairs"]
                }

            old_pairs, new_pairs = pair_map(previous), pair_map(current)
            self.assertTrue(old_pairs.keys() <= new_pairs.keys())
            rejected_names = {
                pair_key(pair)
                for pair in previous["candidate_decisions"]
                if not pair["accepted"]
                and pair["reason"] == "incompatible_names_need_review"
            }
            by_id = {
                (row["source_id"], row["source_record_id"]): row
                for path in paths
                for row in (json.loads(line) for line in path.read_bytes().splitlines())
            }
            for key in new_pairs.keys() - old_pairs.keys():
                self.assertIn(key, rejected_names)
                pair = new_pairs[key]
                target, identifier = pair["anchor"]
                group, field = target.split(".")
                records = [by_id[endpoint] for endpoint in key]
                for row in records:
                    self.assertEqual(row[group][field], identifier)
                # Distinct aliases are legitimate; they never supply an anchor
                # or permit incompatible registered names to be accepted.
                self.assertTrue(any(row["entity"].get("trading_name") for row in records))
                legal = [row["entity"].get("legal_name") for row in records]
                self.assertFalse(all(legal))
            for key in old_pairs:
                self.assertEqual(new_pairs[key]["anchor"], old_pairs[key]["anchor"])

            def members(graph):
                return {
                    (member["source_id"], member["source_record_id"])
                    for group in graph["links"]
                    for member in group["members"]
                }

            old_members, new_members = members(previous), members(current)
            self.assertTrue(old_members <= new_members)
            self.assertEqual(
                result["unlinked_count"],
                expected["unlinked_count"] - len(new_members - old_members),
            )
            self.assertEqual(result["link_count"], len(current["links"]))
            self.assertEqual(result["profile_population"], result["link_count"])
