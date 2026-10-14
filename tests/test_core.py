"""Invented inputs protect native/AST/identity boundaries; no model-quality claims."""

import copy
import json
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from firmable_runtime.common import Failure, canonical, digest, strict_json
from firmable_runtime.engine import (
    compile_ast,
    compile_mappings,
    eval_ast,
    extract_records,
    valid_abn,
    valid_acn,
)
from firmable_runtime.native import (
    field_state,
    partition_records,
    public_structure,
    read_native,
)
from firmable_runtime.identity import build_profile_view, link_records, withdraw_view
from firmable_runtime.semantic_support import subject_support


def xlsx(path, sheets, date1904=False, extra_cell=""):
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as archive:
        book = f'<workbook xmlns="{main}" xmlns:r="{rel}"><workbookPr date1904="{int(date1904)}"/><sheets>'
        relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        for number, (name, rows) in enumerate(sheets.items(), 1):
            book += (
                f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>'
            )
            relationships += f'<Relationship Id="rId{number}" Target="worksheets/sheet{number}.xml" Type="{rel}/worksheet"/>'
            sheet = f'<worksheet xmlns="{main}"><sheetData>'
            for index, values in enumerate(rows, 1):
                sheet += f'<row r="{index}">'
                for column, value in enumerate(values):
                    sheet += f'<c r="{chr(65 + column)}{index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                if index == 2:
                    sheet += extra_cell
                sheet += "</row>"
            archive.writestr(
                f"xl/worksheets/sheet{number}.xml", sheet + "</sheetData></worksheet>"
            )
        archive.writestr("xl/workbook.xml", book + "</sheets></workbook>")
        archive.writestr(
            "xl/_rels/workbook.xml.rels", relationships + "</Relationships>"
        )


def record(source, number, raw):
    return {"source_id": source, "source_record_id": str(number), "raw_record": raw}


def obs(
    source,
    number,
    values,
    confidence=0.9,
    reliability=0.5,
    basis="ingested_at_proxy",
    time="2026-01-01T00:00:00+00:00",
):
    return {
        **record(source, number, dict(values)),
        "values": values,
        "field_confidence": {field: confidence for field in values},
        "source_reliability": reliability,
        "observed_at": time,
        "observed_at_basis": basis,
    }


class CoreTests(unittest.TestCase):
    def test_strict_json_and_hash(self):
        for invalid in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}'):
            with self.assertRaises(Failure):
                strict_json(invalid)
        self.assertEqual(digest({"b": "字", "a": 1}), digest({"a": 1, "b": "字"}))

    def test_csv_tsv_native_text_and_stable_locator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "arbitrary.tsv"
            path.write_text(
                "Ref\tRegistered legal name\tPostcode\n1\tOne Pty Ltd\t0800\n1\tTwo Pty Ltd\t \n2\tThree Pty Ltd\t2000\n"
            )
            table = read_native(directory, path.name, "not-a-semantic-selector")[0]
            self.assertEqual(table["records"][0]["raw_record"]["Postcode"], "0800")
            self.assertEqual(table["records"][1]["raw_record"]["Postcode"], "")
            self.assertNotEqual(
                table["records"][0]["source_record_id"],
                table["records"][1]["source_record_id"],
            )
            again = read_native(directory, path.name, "renamed-source")[0]
            self.assertEqual(
                table["records"][0]["source_record_id"],
                again["records"][0]["source_record_id"],
            )
            self.assertNotIn("One Pty Ltd", json.dumps(public_structure([table])))

    def test_json_preserves_raw_and_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = {
                "a.b": "literal",
                "address": {"zip": 800},
                "blank": None,
                "tags": ["x"],
            }
            Path(directory, "data.json").write_bytes(
                canonical({"outer": {"items": [raw, {"a.b": "other"}]}})
            )
            table = read_native(directory, "data.json", "x")[0]
            self.assertEqual(table["record_pointer"], "/outer/items")
            self.assertEqual(table["records"][0]["raw_record"], raw)
            self.assertEqual(
                eval_ast({"op": "field", "name": "/address/zip"}, raw), 800
            )
            self.assertEqual(eval_ast({"op": "field", "name": "/a.b"}, raw), "literal")

    def test_xlsx_native_multisheet_title_and_small_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "odd.xlsx")
            xlsx(
                path,
                {
                    "Details": [["About"], ["Not data"]],
                    "Entries": [
                        ["Release 2026"],
                        ["Ref", "Registered legal name", "Code"],
                        ["1", "One", "0800"],
                        ["2", "Two", ""],
                    ],
                    "Small changes": [
                        ["Ref", "Registered legal name"],
                        ["3", "Three"],
                        ["4", "Four"],
                    ],
                },
            )
            tables = read_native(directory, path.name, "x")
            self.assertEqual(len(tables), 3)
            self.assertEqual(tables[1]["header_row"], 2)
            self.assertEqual(len(tables[2]["records"]), 2)
            self.assertEqual(tables[1]["records"][1]["raw_record"]["Code"], "")
            self.assertEqual(tables[1]["records"][0]["locator"]["row"], 3)

    def test_formula_without_cache_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "formula.xlsx")
            xlsx(
                path,
                {"Data": [["A", "B"], ["1", "Two"]]},
                extra_cell='<c r="C2"><f>1+1</f></c>',
            )
            with self.assertRaisesRegex(Failure, "no cached"):
                read_native(directory, path.name, "x")

    def test_three_rows_and_duplicates_partition_by_business_content(self):
        rows = [
            record("s", "1", {"value": "a"}),
            record("s", "2", {"value": "a"}),
            record("s", "3", {"value": "b"}),
        ]
        proposal, validation = partition_records(rows, exposed_ids=["1"])
        self.assertEqual({r["source_record_id"] for r in proposal}, {"1", "2"})
        self.assertEqual({r["source_record_id"] for r in validation}, {"3"})
        with self.assertRaises(Failure):
            partition_records(rows[:2])

    def test_ast_executes_checksum_join_condition_and_coverage(self):
        self.assertTrue(valid_abn("61000000002"))
        self.assertTrue(valid_acn("000000000"))
        ast = {
            "op": "when",
            "condition": {
                "op": "is_present",
                "input": {"op": "field", "name": "Number"},
            },
            "then": {
                "op": "validate_abn",
                "input": {
                    "op": "strip_id_separators",
                    "input": {"op": "field", "name": "Number"},
                },
            },
            "else": {"op": "omit", "reason": "Missing number"},
        }
        paths = compile_ast(ast, {"Number"}, ["Number"])
        counts = Counter()
        self.assertEqual(
            eval_ast(ast, {"Number": "61 000 000 002"}, counts), "61000000002"
        )
        self.assertEqual(counts["/then"], 1)
        self.assertEqual(counts["/else"], 0)
        self.assertIn("/else", paths)
        self.assertIsNone(eval_ast(ast, {"Number": "61000000003"}))
        join = {
            "op": "join_address",
            "inputs": [
                {"op": "field", "name": "Street"},
                {"op": "field", "name": "Town"},
            ],
            "require_all": True,
            "on_missing": "omit",
        }
        compile_ast(join, {"Street", "Town"}, ["Street", "Town"])
        self.assertEqual(
            eval_ast(join, {"Street": "1 Main St", "Town": "Example"}),
            "1 Main St, Example",
        )
        self.assertIsNone(eval_ast(join, {"Street": "1 Main St", "Town": ""}))
        with self.assertRaises(Failure):
            compile_ast({"op": "eval", "code": "print(1)"}, set(), [])
        with self.assertRaises(Failure):
            compile_ast(join, {"Street", "Town"}, ["Street"])

    def test_engine_preserves_invalid_raw_and_rejects_semantic_hazards(self):
        mapping = {
            "canonical_field": "entity.abn",
            "source_fields": ["ABN"],
            "confidence": 0.9,
            "semantic_evidence": "ABN",
            "ast": {
                "op": "validate_abn",
                "input": {
                    "op": "strip_id_separators",
                    "input": {"op": "field", "name": "ABN"},
                },
            },
        }
        compiled = compile_mappings([mapping], ["ABN"])
        raw = {"ABN": "61 000 000 003"}
        outputs, report = extract_records(
            [record("s", "1", raw)], compiled, ingested_at="2026-01-01T00:00:00+00:00"
        )
        self.assertEqual(outputs[0]["raw_record"], raw)
        self.assertNotIn("entity.abn", outputs[0]["values"])
        self.assertEqual(report["executed_records"], 1)
        bad = {
            "canonical_field": "entity.status",
            "source_fields": ["Licence status"],
            "confidence": 1,
            "semantic_evidence": "Licence status",
            "ast": {"op": "field", "name": "Licence status"},
        }
        with self.assertRaisesRegex(Failure, "Object status"):
            compile_mappings([bad], ["Licence status"])
        generic = {
            **bad,
            "source_fields": ["status"],
            "semantic_evidence": "status",
            "ast": {"op": "field", "name": "status"},
        }
        with self.assertRaisesRegex(Failure, "Generic status"):
            compile_mappings(
                [generic],
                ["status"],
                documentation="Register of issued licences. status concerns licence validity.",
            )
        with self.assertRaises(Failure):
            compile_mappings([mapping], ["ABN", "Unexplained"])

    def test_licence_unknown_abstention_is_not_an_entity_status_assertion(self):
        mapping = {
            "canonical_field": "entity.status",
            "source_fields": ["Licence status"],
            "confidence": 0.5,
            "semantic_evidence": "Licence status",
            "claim_kind": "unknown_abstention",
            "ast": {
                "op": "enum_lookup",
                "input": {"op": "field", "name": "Licence status"},
                "values": {},
                "default": "unknown",
            },
        }
        compiled = compile_mappings([mapping], ["Licence status"])
        observations, report = extract_records(
            [record("s", 1, {"Licence status": "current"})], compiled
        )
        self.assertEqual(observations[0]["values"]["entity.status"], "unknown")
        self.assertTrue(report["validation_pass"])
        mapping["ast"]["values"]["current"] = "active"
        with self.assertRaisesRegex(Failure, "only unknown"):
            compile_mappings([mapping], ["Licence status"])

    def test_linking_same_anchor_name_only_and_conflict(self):
        values = {
            "entity.abn": "61000000002",
            "entity.legal_name": "One Testing Pty Ltd",
        }
        rows = [
            obs("a", 1, values),
            obs("b", 1, values),
            obs("c", 1, {"entity.legal_name": "One Testing Pty Ltd"}),
        ]
        result = link_records(rows)
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(len(result["links"][0]["pairs"]), 1)
        self.assertEqual(result["unlinked"][0]["source_id"], "c")
        rows[1]["values"]["entity.acn"] = "000000000"
        rows[0] = copy.deepcopy(rows[0])
        rows[0]["values"]["entity.acn"] = "000000019"
        self.assertTrue(valid_acn("000000019"))
        self.assertEqual(link_records(rows)["links"], [])

    def test_multi_anchor_bridge_is_not_transitively_merged(self):
        rows = [
            obs("a", 1, {"entity.abn": "61000000002"}),
            obs("b", 1, {"entity.abn": "61000000002", "entity.acn": "000000000"}),
            obs("c", 1, {"entity.acn": "000000000"}),
        ]
        result = link_records(rows)
        self.assertEqual(result["links"], [])
        self.assertEqual(len(result["unlinked"]), 3)

    def test_canonical_identifier_without_actual_raw_support_is_not_an_anchor(self):
        rows = [
            obs("a", 1, {"entity.abn": "61000000002"}),
            obs("b", 1, {"entity.abn": "61000000002"}),
        ]
        rows[1]["raw_record"] = {"ABN": "51111111113"}
        self.assertEqual(link_records(rows)["links"], [])

    def test_profiles_conflicts_and_source_withdrawal_recompute(self):
        rows = [
            obs(
                "a",
                1,
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "One Testing Pty Ltd",
                    "address.locality": "First",
                },
                reliability=0.9,
            ),
            obs(
                "b",
                1,
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "One Testing Pty Ltd",
                    "address.locality": "Second",
                },
                reliability=0.7,
            ),
        ]
        before_bytes = canonical(rows)
        linking = link_records(rows)
        profiles = build_profile_view(rows, linking)["profiles"]
        self.assertEqual(profiles[0]["fields"]["address.locality"]["value"], "First")
        self.assertEqual(
            len(profiles[0]["fields"]["entity.legal_name"]["provenance"]), 2
        )
        withdrawn = withdraw_view(profiles, rows, linking, "a")
        self.assertEqual(
            withdrawn["profiles"][0]["fields"]["address.locality"]["value"], "Second"
        )
        self.assertEqual(withdrawn["fields_changed"], 1)
        self.assertEqual(withdrawn["profiles_affected"], 1)
        self.assertEqual(
            withdrawn["profiles"][0]["entity_key"], profiles[0]["entity_key"]
        )
        self.assertNotIn('"source_id":"a"', canonical(withdrawn["profiles"]).decode())
        self.assertEqual(before_bytes, canonical(rows))

    def test_source_time_wins_only_when_comparable(self):
        rows = [
            obs(
                "a",
                1,
                {"entity.abn": "61000000002", "address.locality": "Older"},
                basis="source_stated",
            ),
            obs(
                "b",
                1,
                {"entity.abn": "61000000002", "address.locality": "Newer"},
                basis="source_stated",
                time="2026-02-01T00:00:00+00:00",
            ),
        ]
        linking = link_records(rows)
        self.assertEqual(
            build_profile_view(rows, linking)["profiles"][0]["fields"][
                "address.locality"
            ]["value"],
            "Newer",
        )
        rows[1]["observed_at_basis"] = "ingested_at_proxy"
        self.assertEqual(
            build_profile_view(rows, linking)["profiles"][0]["fields"][
                "address.locality"
            ]["value"],
            "Older",
        )

    def test_symlink_and_escape_evidence_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "real.csv").write_text("Key,Value\n1,One\n2,Two\n")
            Path(directory, "link.csv").symlink_to(Path(directory, "real.csv"))
            for path in ("link.csv", "../real.csv", "/real.csv"):
                with self.assertRaises(Failure):
                    read_native(directory, path, "x")


def field(name):
    return {"op": "field", "name": name}


def mapping(name, target, ast=None, dependencies=None):
    return {
        "source_field": name,
        "source_fields": dependencies or [name],
        "canonical_field": target,
        "confidence": 0.8,
        "semantic_evidence": name,
        "ast": ast or field(name),
    }


def identifier(name):
    return mapping(name, "entity.acn", {"op": "validate_acn", "input": field(name)})


class SubjectAssociationTests(unittest.TestCase):
    def compile(self, values, docs, inventory=None):
        inventory = inventory or sorted(
            {name for value in values for name in value["source_fields"]}
        )
        return compile_mappings(
            values,
            inventory,
            docs,
            unmapped=[
                {"source_field": name, "reason": "Unrelated native evidence"}
                for name in inventory
                if all(name not in value["source_fields"] for value in values)
            ],
        )

    def refused(self, values, docs, inventory=None, reason=None):
        with (
            patch("firmable_runtime.engine.eval_ast", wraps=eval_ast) as execute,
            self.assertRaises(Failure) as caught,
        ):
            self.compile(values, docs, inventory)
        execute.assert_not_called()
        if reason:
            self.assertIn(reason, str(caught.exception))
        return caught.exception.details["findings"]

    def test_same_opaque_field_and_value_have_different_documented_owners(self):
        value = mapping("X", "address.locality")
        for owner in (
            "venue",
            "premises",
            "permit",
            "individual",
            "contract",
            "contact",
        ):
            with self.subTest(owner=owner):
                self.refused(
                    [value],
                    "X: The suburb of the " + owner + ".",
                    reason="same business subject",
                )
        self.compile([value], "X: The suburb of the company.")

    def test_business_website_url_relationship_is_target_specific(self):
        for owner in ("company", "business", "supplier's"):
            with self.subTest(owner=owner):
                compiled = self.compile(
                    [mapping("X", "entity.website")],
                    "X: The URL of the " + owner + " website.",
                )
                self.assertEqual(
                    compiled[0]["semantic_support"]["status"], "explicit_entity"
                )
        self.compile(
            [mapping("Company Website", "entity.website")],
            "Company Website: The URL of the company website.",
        )
        for target in ("address.postcode", "entity.legal_name"):
            with self.subTest(target=target):
                result = subject_support(
                    mapping("X", target), "X: The name of the company website.", ["X"]
                )
                self.assertFalse(result["supported"])

    def test_website_relationship_preserves_immediate_owner_and_roles(self):
        for owner in ("company director", "business venue", "company or individual"):
            with self.subTest(owner=owner):
                self.refused(
                    [mapping("X", "entity.website")],
                    "X: The URL of the " + owner + " website.",
                )
        self.refused(
            [identifier("I"), mapping("X", "entity.website")],
            "I: The Australian company number of the supplier.\nX: The URL of the buyer website.",
            reason="Mixed subject roles",
        )

    def test_conditional_predicate_is_not_an_output_owner(self):
        ast = {
            "op": "when",
            "condition": {"op": "is_present", "input": field("Flag")},
            "then": field("X"),
            "else": {"op": "omit", "reason": "No value"},
        }
        value = mapping("X", "address.postcode", ast, ["X", "Flag"])
        self.compile(
            [identifier("I"), value],
            "I: The Australian company number of the supplier.\nX: The postcode of the supplier.\nFlag: A buyer reference.",
        )

    def test_conditional_output_branches_keep_their_own_subjects(self):
        ast = {
            "op": "when",
            "condition": {"op": "is_present", "input": field("Flag")},
            "then": field("X"),
            "else": field("Y"),
        }
        value = mapping("X", "address.postcode", ast, ["X", "Y", "Flag"])
        self.refused(
            [value],
            "X: The postcode of the supplier.\nY: The postcode of the buyer.\nFlag: Internal indicator.",
            reason="Mixed subject roles",
        )

    def test_unrelated_document_definition_cannot_supply_or_conflict_owner(self):
        docs = "X: The postcode of the supplier.\nOther: The full business address of the venue."
        self.compile([mapping("X", "address.postcode")], docs, ["X", "Other"])
        self.refused(
            [mapping("X", "address.postcode")],
            "X: The postcode of the venue.\nOther: The postcode of the company.",
            ["X", "Other"],
        )

    def test_generic_existing_paths_are_explicitly_unresolved_not_certified(self):
        compiled = self.compile([mapping("X", "address.postcode")], "")
        self.assertEqual(compiled[0]["semantic_support"]["status"], "unresolved")
        self.assertIn("not verified", compiled[0]["semantic_support"]["limitation"])

    def test_complete_owner_head_prevents_entity_prefix_bypass(self):
        for owner in ("business venue", "supplier's director", "company website"):
            with self.subTest(owner=owner):
                self.refused(
                    [mapping("X", "address.postcode")],
                    "X: The postcode of the " + owner + ".",
                )

    def test_business_owner_head_can_have_nonbusiness_modifiers(self):
        for owner in ("venue-owning company", "individual sole trader"):
            with self.subTest(owner=owner):
                compiled = self.compile(
                    [mapping("X", "address.postcode")],
                    "X: The postcode of the " + owner + ".",
                )
                self.assertEqual(
                    compiled[0]["semantic_support"]["status"], "explicit_entity"
                )


    def test_whole_address_property_does_not_require_completeness_adjective(self):
        for name in ("Company address", "Registrant address", "Applicant address"):
            with self.subTest(name=name):
                expression = {
                    "op": "collapse_ws",
                    "input": {"op": "trim", "input": field(name)},
                }
                self.compile([mapping(name, "address.full", expression)], "")
                raw = {name: "  Exampletown  NSW  2000  "}
                original = copy.deepcopy(raw)
                self.assertEqual(eval_ast(expression, raw), "Exampletown NSW 2000")
                self.assertEqual(raw, original)
        self.compile(
            [mapping("Destination", "address.full")],
            "Destination: Address of the company.",
        )
        self.compile(
            [mapping("Company address", "address.full")],
            "Company address: The business address including city, state and postcode.",
        )

    def test_address_components_nonpostal_values_and_foreign_owners_stay_blocked(self):
        for name, docs in (
            ("Company address line 1", ""),
            ("Company email address", ""),
            ("Company IP address", ""),
            ("Company address postcode", ""),
            ("Company address city", ""),
            ("Company address suburb", ""),
            ("Company address latitude", ""),
            ("Company address longitude", ""),
            ("Company address status", ""),
            ("Company address", "Company address: The postcode of the company."),
            ("Company address", "Company address: The postcode within the company address."),
            ("Company address", "Company address: The suburb component of the business address."),
            ("Company address", "Company address: A partial business address."),
            ("Destination", "Destination: Address of the company director."),
            ("Destination", "Destination: The suburb of the company."),
        ):
            with self.subTest(name=name, docs=docs):
                self.refused([mapping(name, "address.full")], docs)

    def test_address_cannot_borrow_another_fields_meaning_or_drop_tokens(self):
        self.refused(
            [mapping("Destination", "address.full")],
            "Other: Address of the company.",
            ["Destination", "Other"],
        )
        findings = self.refused(
            [mapping("Company address", "address.full", {
                "op": "parse_au_location", "input": field("Company address"),
                "component": "locality",
            })],
            "",
        )
        self.assertIn("address_transform_not_whole_field", json.dumps(findings))
        findings = self.refused(
            [mapping("Company locality", "address.full", {
                "op": "join_address",
                "inputs": [field("Company locality"), field("Company postcode")],
                "require_all": True, "on_missing": "omit",
            }, ["Company locality", "Company postcode"])],
            "",
        )
        self.assertIn("joined_address_requires_preserved_same_field", json.dumps(findings))


class DataBoundaryTests(unittest.TestCase):
    def test_literal_fields_and_missing_null_blank_are_distinct(self):
        raw = {"a.b": "literal", "a": {"b": "nested"}, "null": None, "blank": " "}
        self.assertEqual(field_state(raw, "a.b")["value"], "literal")
        self.assertEqual(field_state(raw, "/a/b")["value"], "nested")
        for name, state in [
            ("absent", "missing"),
            ("null", "null"),
            ("blank", "blank"),
        ]:
            self.assertEqual(field_state(raw, name)["state"], state)
        with self.assertRaises(Failure):
            field_state(raw, "/bad~2escape")

    def test_ast_config_is_atomic_and_wire_preserves_raw_and_leading_zero(self):
        from firmable_runtime.execution import (
            compile_config,
            discover,
            run_engine,
            table_schema,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.csv").write_text(
                "Legal name,Postcode\nOne Pty Ltd,0800\nTwo Pty Ltd,2000\n"
            )
            (root / "docs.txt").write_text(
                "Legal name: Registered legal name of the company.\nPostcode: The postcode of the company."
            )
            tables, paths, _, docs = discover(root, "invented")
            mappings = [
                {
                    **mapping(name, target),
                    "transformation": "Copy native text",
                    "execution_pointer": "/execution/transforms/" + str(index),
                    "validation_pass": False,
                }
                for index, (name, target) in enumerate(
                    [
                        ("Legal name", "entity.legal_name"),
                        ("Postcode", "address.postcode"),
                    ]
                )
            ]
            for item in mappings:
                item.pop("ast")
            config = {
                "source_schema": table_schema(tables, [0], paths),
                "field_mappings": mappings,
                "unmapped_fields": [],
                "execution": {"transforms": [field("Legal name"), field("Postcode")]},
            }
            records = tables[0]["records"]
            before = copy.deepcopy(records)
            observations, report = run_engine(
                config, records, docs, {}, "2026-10-13T00:00:00+00:00"
            )
            self.assertEqual(records, before)
            self.assertTrue(report["validation_pass"])
            self.assertEqual(observations[0]["address"]["postcode"], "0800")
            self.assertEqual(observations[0]["raw_record"], records[0]["raw_record"])
            config["execution"]["transforms"][1] = field("Missing")
            with (
                patch("firmable_runtime.engine.eval_ast", wraps=eval_ast) as execute,
                self.assertRaises(Failure),
            ):
                compile_config(config, docs)
            execute.assert_not_called()

    def test_shared_identifier_does_not_override_company_person_conflict(self):
        values = {"entity.abn": "51824753556", "entity.entity_type": "company"}
        rows = [
            obs("company-source", 1, values),
            obs("licence-source", 1, {"entity.abn": values["entity.abn"]}),
        ]
        rows[1]["raw_record"] = {
            "ABN": values["entity.abn"],
            "Licence Type DESC": "Individual",
            "Licensee Name": "Invented Person",
            "Licence Number": "SYN-1",
        }
        before = copy.deepcopy(rows)
        result = link_records(rows)
        self.assertEqual(result["links"], [])
        self.assertEqual(
            result["candidate_decisions"][0]["reason"],
            "person_company_subject_conflict",
        )
        self.assertEqual(rows, before)

    def test_workflow_links_profiles_and_withdrawal_accept_wire_observations(self):
        from firmable_runtime.execution import wire_observations
        from firmable_runtime.workflow import build_outputs, withdraw_outputs

        with tempfile.TemporaryDirectory() as directory:
            root, observations = Path(directory), []
            compiled = compile_mappings(
                [
                    mapping(
                        "ABN",
                        "entity.abn",
                        {"op": "validate_abn", "input": field("ABN")},
                    )
                ],
                ["ABN"],
                "ABN: Australian Business Number of the company.",
            )
            for source in ("invented-a", "invented-b"):
                internal, _ = extract_records(
                    [record(source, 1, {"ABN": "51824753556"})],
                    compiled,
                    licence="CC-BY",
                    ingested_at="2026-10-13T00:00:00+00:00",
                )
                observations.extend(wire_observations(internal))
            path = root / "observations.json"
            path.write_bytes(canonical(observations))
            summary = build_outputs([path], root / "outputs")
            self.assertEqual((summary["link_count"], summary["profile_count"]), (1, 1))
            linked = strict_json((root / "outputs" / "links.json").read_bytes())
            result = withdraw_outputs([path], "invented-a", root / "withdraw")
            self.assertEqual(
                result["identity_review_required"], [linked["links"][0]["entity_key"]]
            )

    def test_final_validation_annotation_cannot_be_claimed_by_model(self):
        from firmable_runtime.execution import compile_config
        from firmable_runtime.llm_route import config_schema, validate_config
        from firmable_runtime.schema import check_response, proposal_schema

        item = {
            **mapping("Legal name", "entity.legal_name"),
            "transformation": "Copy original legal name",
            "execution_pointer": "/execution/transforms/0",
            "validation_pass": False,
        }
        item.pop("ast")
        config = {
            "field_mappings": [item],
            "unmapped_fields": [],
            "execution": {"transforms": [field("Legal name")]},
            "source_schema": {
                "format": "csv",
                "fields": [{"source_field": "Legal name", "inferred_type": "text"}],
                "data_files": ["invented.csv"],
                "discovery_method": "Invented test structure",
            },
        }
        docs = "Legal name: Registered legal name of the company in this record."
        for kind in ("ast", "llm_task"):
            candidate = copy.deepcopy(config)
            if kind == "llm_task":
                candidate["execution"] = {
                    "kind": kind,
                    "task": "Copy exact registered legal name.",
                    "subject": "The company described by this record",
                }
                candidate["field_mappings"][0]["execution_pointer"] = "/execution/task"
            for flag in (False, True):
                with self.subTest(kind=kind, validation_pass=flag):
                    candidate["field_mappings"][0]["validation_pass"] = flag
                    if kind == "ast":
                        self.assertEqual(len(compile_config(candidate, docs)), 1)
                    else:
                        self.assertEqual(validate_config(candidate, docs), candidate)
                    proposal = {
                        key: value
                        for key, value in candidate.items()
                        if key != "source_schema"
                    }
                    schema = proposal_schema()
                    if kind == "llm_task":
                        schema = config_schema(
                            schema["properties"]["field_mappings"]["items"]
                        )
                    if flag:
                        with self.assertRaises(Failure):
                            check_response(proposal, schema)
                    else:
                        self.assertEqual(check_response(proposal, schema), proposal)
