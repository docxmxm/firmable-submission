"""Anonymous native layouts and bounded recovery; no real-source mapping hints."""

import copy
import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings
from firmable_runtime.execution import discover
from firmable_runtime.llm_route import validate_config as validate_llm_config
from firmable_runtime.native import field_state, public_structure, read_native
from test_agent import NativeFixture, ScriptedProvider
from test_core import xlsx
from test_llm import mapping as llm_mapping, task_config


def native_file(root, extension, rows):
    path = Path(root, "input." + extension)
    if extension == "xlsx":
        xlsx(path, {"Entries": rows})
    else:
        with path.open("w", newline="") as stream:
            csv.writer(stream, delimiter="\t" if extension == "tsv" else ",").writerows(
                rows
            )
    return path


def merge_cells(path, reference):
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    name = "xl/worksheets/sheet1.xml"
    parts[name] = parts[name].replace(
        b"</worksheet>",
        (
            '<mergeCells count="1"><mergeCell ref="'
            + reference
            + '"/></mergeCells></worksheet>'
        ).encode(),
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in parts.items():
            archive.writestr(name, contents)


class NativeLayoutTests(unittest.TestCase):
    def test_repeated_text_headers_are_not_replaced_by_the_first_data_row(self):
        for extension in ("csv", "tsv", "xlsx"):
            with self.subTest(format=extension), tempfile.TemporaryDirectory() as root:
                path = native_file(
                    root,
                    extension,
                    [
                        ["Role", "Label", "Label"],
                        ["Owner", "First", "Other"],
                        ["Agent", "Second", "Another"],
                    ],
                )
                table = read_native(root, path.name, "anonymous")[0]
                self.assertEqual(table["header_row"], 1)
                self.assertEqual(len(table["records"]), 2)

    def test_an_ambiguous_sheet_does_not_abort_other_readable_sheets(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "input.xlsx")
            xlsx(
                path,
                {
                    "Unknown": [
                        ["Heading", "Details"],
                        ["Note", "Text"],
                        [],
                        ["More", "Text"],
                    ],
                    "Entries": [["Ref", "Label"], ["001", "First"], ["002", "Second"]],
                },
            )
            tables = read_native(root, path.name, "anonymous")
            self.assertEqual(tables[0]["status"], "unsupported_table")
            self.assertEqual(tables[0]["records"], [])
            self.assertIn("reader_issue", public_structure(tables)[0])
            self.assertEqual(tables[1]["status"], "table")
            self.assertEqual(len(tables[1]["records"]), 2)

    def test_duplicate_headers_keep_distinct_physical_columns(self):
        for extension in ("csv", "tsv", "xlsx"):
            with self.subTest(format=extension), tempfile.TemporaryDirectory() as root:
                path = native_file(
                    root,
                    extension,
                    [
                        ["Owner", "ABN", "Agent", "ABN"],
                        ["First", "61 000 000 002", "Other", "51 824 753 556"],
                        ["Second", "51 824 753 556", "Another", "61 000 000 002"],
                    ],
                )
                table = read_native(root, path.name, "anonymous")[0]
                self.assertEqual(table["status"], "table")
                self.assertEqual(len(set(table["fields"])), 4)
                self.assertNotIn("ABN", table["fields"])
                columns = table["columns"]
                self.assertEqual(
                    [c["header"] for c in columns], ["Owner", "ABN", "Agent", "ABN"]
                )
                self.assertEqual([c["column"] for c in columns], [1, 2, 3, 4])
                first, second = columns[1]["source_field"], columns[3]["source_field"]
                raw = table["records"][0]["raw_record"]
                self.assertEqual(
                    (raw[first], raw[second]), ("61 000 000 002", "51 824 753 556")
                )
                self.assertNotEqual(first, second)
                self.assertEqual(table, read_native(root, path.name, "anonymous")[0])
                self.assertEqual(public_structure([table])[0]["columns"], columns)
                self.assertNotIn(
                    "61 000 000 002", json.dumps(public_structure([table]))
                )
                # A generated physical key is not a fabricated semantic header.
                mapping = {
                    "source_field": first,
                    "source_fields": [first],
                    "canonical_field": "entity.abn",
                    "confidence": 0.9,
                    "semantic_evidence": first,
                    "ast": {
                        "op": "validate_abn",
                        "input": {"op": "field", "name": first},
                    },
                }
                with self.assertRaises(Failure):
                    compile_mappings(
                        [mapping],
                        table["fields"],
                        unmapped=[
                            {"source_field": f, "reason": "Unresolved subject."}
                            for f in table["fields"]
                            if f != first
                        ],
                    )

    def test_blank_headers_and_overflow_columns_retain_every_value(self):
        for extension in ("csv", "tsv", "xlsx"):
            with self.subTest(format=extension), tempfile.TemporaryDirectory() as root:
                expected = ["001", " \t ", "First, line\nbreak", "trailing value"]
                path = native_file(
                    root,
                    extension,
                    [["Reference", "", "Label"], expected, ["002", "", "Second"]],
                )
                table = read_native(root, path.name, "anonymous")[0]
                self.assertEqual(table["status"], "table")
                self.assertEqual(table["header_row"], 1)
                self.assertEqual(
                    [c["header"] for c in table["columns"]],
                    ["Reference", "", "Label", None],
                )
                raw = table["records"][0]["raw_record"]
                self.assertEqual([raw[f] for f in table["fields"]], expected)
                self.assertEqual(field_state(raw, table["fields"][1])["state"], "blank")
                self.assertEqual(
                    table["records"][1]["raw_record"][table["fields"][3]], ""
                )

    def test_wider_rows_do_not_hide_a_unique_header(self):
        for extension in ("csv", "tsv", "xlsx"):
            with self.subTest(format=extension), tempfile.TemporaryDirectory() as root:
                path = native_file(
                    root,
                    extension,
                    [
                        ["Reference", "ABN"],
                        ["001", "61000000002", "unlabelled"],
                        ["002", "51824753556", "extra"],
                    ],
                )
                table = read_native(root, path.name, "anonymous")[0]
                self.assertEqual(table["status"], "table")
                self.assertEqual(table["fields"][:2], ["Reference", "ABN"])
                self.assertEqual(len(table["records"]), 2)
                self.assertEqual(table["records"][0]["locator"]["row"], 2)
                self.assertEqual(table["columns"][2]["header"], None)

    def test_clean_native_cells_keep_whitespace_and_leading_zeroes(self):
        for extension in ("csv", "tsv", "xlsx"):
            with self.subTest(format=extension), tempfile.TemporaryDirectory() as root:
                path = native_file(
                    root,
                    extension,
                    [["Reference", "Text"], ["001", " \t "], ["002", "second"]],
                )
                table = read_native(root, path.name, "anonymous")[0]
                self.assertEqual(
                    table["records"][0]["raw_record"],
                    {"Reference": "001", "Text": " \t "},
                )

    def test_json_keeps_nested_records_and_separate_record_arrays(self):
        with tempfile.TemporaryDirectory() as root:
            records = [
                {
                    "ref": "001",
                    "address": {"postcode": "0800"},
                    "roles": [{"name": "Other"}],
                    "blank": " \t ",
                    "null": None,
                },
                {"ref": "002"},
            ]
            path = Path(root, "nested.json")
            path.write_text(
                json.dumps({"payload": {"items": records}, "other": [{"ref": "003"}]})
            )
            tables = read_native(root, path.name, "anonymous")
            self.assertEqual(
                [t["record_pointer"] for t in tables], ["/payload/items", "/other"]
            )
            self.assertEqual([r["raw_record"] for r in tables[0]["records"]], records)
            self.assertEqual(
                field_state(records[0], "address.postcode")["value"], "0800"
            )
            self.assertEqual(field_state(records[1], "null")["state"], "missing")

    def test_xlsx_merged_title_is_preserved_without_filling_data_cells(self):
        with tempfile.TemporaryDirectory() as root:
            path = native_file(
                root,
                "xlsx",
                [
                    ["Register title"],
                    ["Reference", "Label"],
                    ["001", "First"],
                    ["002", "Second"],
                ],
            )
            merge_cells(path, "A1:B1")
            table = read_native(root, path.name, "anonymous")[0]
            self.assertEqual(table["header_row"], 2)
            self.assertEqual(len(table["records"]), 2)
            self.assertEqual(table["details"]["merged_ranges"], ["A1:B1"])
            self.assertEqual(
                table["records"][0]["raw_record"],
                {"Reference": "001", "Label": "First"},
            )

    def test_xlsx_merge_in_data_keeps_anchor_values_without_imputing_owners(self):
        with tempfile.TemporaryDirectory() as root:
            path = native_file(
                root,
                "xlsx",
                [["Reference", "Label"], ["001", "First"], ["002", ""]],
            )
            merge_cells(path, "B2:B3")
            table = read_native(root, path.name, "anonymous")[0]
            self.assertEqual(table["records"][0]["raw_record"]["Label"], "First")
            self.assertEqual(table["records"][1]["raw_record"]["Label"], "")
            structure = public_structure([table])[0]
            self.assertEqual(structure["merged_ranges"], ["B2:B3"])
            self.assertIn("no_fill", structure["merge_policy"])

    def test_xlsx_merged_header_retains_unlabelled_physical_values(self):
        with tempfile.TemporaryDirectory() as root:
            path = native_file(
                root,
                "xlsx",
                [
                    ["Ref", "Entity", "", "Place"],
                    ["001", "First", "61000000002", "Town"],
                    ["002", "Second", "51824753556", "City"],
                ],
            )
            merge_cells(path, "B1:C1")
            table = read_native(root, path.name, "anonymous")[0]
            field = table["columns"][2]["source_field"]
            self.assertEqual(table["columns"][2]["header"], "")
            self.assertEqual(table["records"][0]["raw_record"][field], "61000000002")
            self.assertEqual(table["merged_ranges"], ["B1:C1"])

    def test_mixed_supported_files_are_all_discovered(self):
        with tempfile.TemporaryDirectory() as root:
            for extension in ("csv", "tsv", "xlsx"):
                native_file(
                    root,
                    extension,
                    [["Ref", "Label"], ["001", "First"], ["002", "Second"]],
                )
            Path(root, "input.json").write_text('[{"Ref":"003"},{"Ref":"004"}]')
            tables, paths, _, _ = discover(root, "anonymous")
            self.assertEqual(
                set(paths), {"input.csv", "input.tsv", "input.xlsx", "input.json"}
            )
            self.assertEqual(sum(len(t["records"]) for t in tables), 8)


class SelectionProvider(ScriptedProvider):
    def __init__(self, fixture, selections):
        super().__init__(fixture, actions=["stop"])
        self.selections = list(selections)

    def generate(self, request, call_id):
        if request["purpose"] != "inspect":
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        selected = self.selections.pop(0)
        if isinstance(selected, Failure):
            raise selected
        return {
            "config": {
                "selected_table_ids": selected,
                "reason": "Structural scope choice.",
            },
            "usage": {},
            "cost_usd": 0.0,
        }


class InitialSelectionRecoveryTests(unittest.TestCase):
    def test_no_readable_table_returns_a_gap_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            fixture.native.write_text("")
            provider = SelectionProvider(fixture, [])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["gap"]["kind"], "native_reader_gap")
            self.assertEqual(
                result["gap"]["native_structure"][0]["status"], "no_unambiguous_table"
            )
            self.assertIsNone(result["mapping_config"])
            self.assertFalse(provider.requests)

    def test_generated_column_id_is_not_a_header_in_the_llm_backend(self):
        field = "native-column:" + "a" * 64
        config = task_config([llm_mapping(field, "entity.abn")])
        config["source_schema"] = {
            "format": "csv",
            "data_files": ["input.csv"],
            "discovery_method": "native",
            "fields": [{"source_field": field, "inferred_type": "text"}],
        }
        with self.assertRaisesRegex(Failure, "native header"):
            validate_llm_config(config, "")

    def fixture(self, root):
        fixture = NativeFixture(root)
        Path(fixture.evidence, "zzz.csv").write_text("")
        return fixture

    def test_invalid_initial_choice_gets_bound_feedback_and_can_recover(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = SelectionProvider(fixture, [[0, 1], [0]])
            result = onboard_source(fixture.request, provider)
            calls = [r for r in provider.requests if r["purpose"] == "inspect"]
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                calls[1]["payload"]["selection_correction"]["readable_table_ids"], [0]
            )
            self.assertEqual(
                calls[1]["payload"]["selection_correction"]["previous_response"][
                    "selected_table_ids"
                ],
                [0, 1],
            )
            self.assertEqual(result["table_scope"]["included_table_ids"], [0])
            self.assertEqual(
                result["table_scope"]["unreadable_tables"][0]["table_id"], 1
            )
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["approval"])

    def test_repeated_bad_selection_is_bounded_without_silently_dropping_tables(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = SelectionProvider(fixture, [[0, 1]] * 3)
            with self.assertRaises(Failure) as caught:
                onboard_source(fixture.request, provider)
            self.assertEqual(caught.exception.kind, "validation_error")
            self.assertEqual(len(provider.requests), 3)

    def test_unknown_model_outcome_is_not_resent(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture(root)
            provider = SelectionProvider(
                fixture, [Failure("model_outcome_unknown", "Unknown response")]
            )
            with self.assertRaises(Failure):
                onboard_source(fixture.request, provider)
            self.assertEqual(len(provider.requests), 1)
