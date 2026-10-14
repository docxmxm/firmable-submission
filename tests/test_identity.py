"""Profile abstention keeps observations intact and informative fields usable."""

import copy
import unittest

from firmable_runtime.identity import build_profile_view, link_records, withdraw_view


def observation(source, values, reliability):
    values = {"entity.abn": "61000000002", **values}
    return {
        "source_id": source,
        "source_record_id": "1",
        "raw_record": dict(values),
        "values": values,
        "field_confidence": {field: 0.9 for field in values},
        "source_reliability": reliability,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "observed_at_basis": "ingested_at_proxy",
    }


class ProfileAbstentionTests(unittest.TestCase):
    def test_unknown_enums_are_omitted_without_mutating_observations(self):
        rows = [
            observation(
                "a",
                {
                    "entity.status": "unknown",
                    "entity.entity_type": "unknown",
                    "entity.legal_name": "unknown",
                    "address.state": "OTHER",
                },
                0.9,
            ),
            observation("b", {}, 0.7),
        ]
        original = copy.deepcopy(rows)
        profile = build_profile_view(rows, link_records(rows))["profiles"][0]
        self.assertNotIn("entity.status", profile["fields"])
        self.assertNotIn("entity.entity_type", profile["fields"])
        self.assertEqual(profile["fields"]["entity.legal_name"]["value"], "unknown")
        self.assertEqual(profile["fields"]["address.state"]["value"], "OTHER")
        self.assertEqual(profile["record_count"], 2)
        self.assertEqual(rows, original)

    def test_unknown_cannot_override_known_value_or_return_after_withdrawal(self):
        for field, known in (
            ("entity.status", "active"),
            ("entity.entity_type", "company"),
        ):
            with self.subTest(field=field):
                rows = [
                    observation("unknown-source", {field: "unknown"}, 0.99),
                    observation("known-source", {field: known}, 0.7),
                ]
                original = copy.deepcopy(rows)
                linking = link_records(rows)
                profiles = build_profile_view(rows, linking)["profiles"]
                selected = profiles[0]["fields"][field]
                self.assertEqual(selected["value"], known)
                self.assertEqual(selected["conflicts"], [])
                self.assertEqual(
                    [claim["source_id"] for claim in selected["provenance"]],
                    ["known-source"],
                )
                withdrawn = withdraw_view(profiles, rows, linking, "known-source")
                self.assertNotIn(field, withdrawn["profiles"][0]["fields"])
                self.assertEqual(withdrawn["delta"][0]["fields_lost"], [field])
                self.assertEqual(rows, original)
