"""Table scope decisions preserve additional records with duplicate headers."""

import copy
import csv
import tempfile
import unittest

from firmable_runtime.agent import _check_table_scope
from firmable_runtime.common import Failure
from firmable_runtime.native import public_structure
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from test_agent import NativeFixture, ScriptedProvider


class ScopeProvider(ScriptedProvider):
    def __init__(self, fixture, selection):
        super().__init__(fixture)
        self.selection = selection

    def generate(self, request, call_id):
        self.requests.append(copy.deepcopy(request))
        check_response(self.selection, request["output_schema"])
        return {"config": self.selection, "usage": {}, "cost_usd": 0.0}


class TableScopeTests(unittest.TestCase):
    def run_scope(self, fixture, selection, initial=(0,)):
        provider = ScopeProvider(fixture, selection)
        runtime = Runtime(fixture.request, provider)
        structure = [
            {"table_id": i, **item}
            for i, item in enumerate(public_structure(runtime.tables))
        ]
        ref = runtime.artifact("discovery", {"tables": structure})
        ids = _check_table_scope(
            runtime,
            {
                "selected_table_ids": list(initial),
                "reason": "Primary table already has the fields.",
            },
            structure,
            {},
            ref,
        )
        return ids, runtime, provider

    def fixture(self, root):
        fixture = NativeFixture(root)
        with (fixture.evidence / "zzz.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(fixture.rows[0]))
            writer.writeheader()
            writer.writerow(
                {
                    fixture.field: "Extra Company Pty Ltd",
                    "row_note": "Extra original record",
                }
            )
        return fixture

    def test_same_headers_cannot_silently_remove_an_additional_table(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            ids, runtime, provider = self.run_scope(
                fixture,
                {
                    "selected_table_ids": [0, 1],
                    "reason": "Both contain additional original entities.",
                    "exclusions": [],
                },
            )
            self.assertEqual(ids, [0, 1])
            self.assertEqual(len(provider.requests), 1)
            payload = provider.requests[0]["payload"]
            self.assertIn("scope_review", payload)
            self.assertNotIn("raw_record", str(payload))
            ledger = runtime.run.load_artifact("table-scope")
            self.assertEqual(ledger["selected_record_count"], 9)
            self.assertEqual(ledger["excluded_record_count"], 0)
            self.assertEqual(set(ledger["included_table_ids"]), {0, 1})

    def test_exclusions_need_complete_partition_and_original_evidence(self):
        for bad in ("missing_disposition", "fabricated_quote", "wrong_table"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as root:
                fixture = self.fixture(root)
                exclusions = [
                    {
                        "table_id": 1,
                        "reason": "Definitions",
                        "evidence_quote": "Fabricated table definition.",
                    }
                ]
                if bad == "missing_disposition":
                    exclusions = []
                elif bad == "wrong_table":
                    exclusions[0].update(table_id=0, evidence_quote=fixture.field)
                with self.assertRaises(Failure):
                    self.run_scope(
                        fixture,
                        {
                            "selected_table_ids": [0],
                            "reason": "Excluded",
                            "exclusions": exclusions,
                        },
                    )

    def test_documented_exclusion_is_preserved_with_its_count_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            quote = (
                "zzz.csv contains reference definitions rather than business records."
            )
            with (fixture.evidence / "definitions.md").open("a") as stream:
                stream.write("\n" + quote)
            ids, runtime, _ = self.run_scope(
                fixture,
                {
                    "selected_table_ids": [0],
                    "reason": "Reference material",
                    "exclusions": [
                        {
                            "table_id": 1,
                            "reason": "Out of the business-record scope",
                            "evidence_quote": quote,
                        }
                    ],
                },
            )
            self.assertEqual(ids, [0])
            self.assertEqual(
                runtime.run.load_artifact("table-scope")["excluded_record_count"], 1
            )

    def test_single_in_scope_table_keeps_the_existing_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            ids, runtime, provider = self.run_scope(fixture, {}, initial=(0,))
            self.assertEqual(ids, [0])
            self.assertFalse(provider.requests)
            self.assertEqual(runtime.run.load_artifact("table-scope")["selected_record_count"], 8)
