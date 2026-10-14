"""Scripted raw-span regressions over invented records; no paid/model-quality runs."""

import copy
import tempfile
import unittest
from pathlib import Path

from firmable_runtime import llm_route
from firmable_runtime.common import Failure, byte_hash
from firmable_runtime.execution import run_engine, table_schema, validate_observation
from firmable_runtime.runtime import Runtime
from firmable_runtime.schema import check_response
from firmable_runtime.openrouter_wire import OpenRouterWireContract


def mapping(field="Statement", target="entity.legal_name", pointer="/execution/task"):
    return {
        "source_field": field,
        "source_fields": [field],
        "canonical_field": target,
        "confidence": 0.99,
        "transformation": "Extract exact source text",
        "semantic_evidence": field,
        "execution_pointer": pointer,
        "validation_pass": False,
    }


def task_config(fields=None):
    return {
        "field_mappings": fields or [mapping()],
        "unmapped_fields": [],
        "execution": {
            "kind": "llm_task",
            "task": "Extract the registered name from the statement, preserving original text.",
            "subject": "The company described by the record",
        },
    }


def extracted(request):
    rows = []
    for record in request["payload"]["records"]:
        claims = []
        for item in request["payload"]["task_config"]["field_mappings"]:
            field, target = item["source_field"], item["canonical_field"]
            text = record["raw_record"][field]
            start = text.find(": ") + 2 if ": " in text else 0
            value = text[start:]
            claims.append(
                {
                    "canonical_field": target,
                    "value": value,
                    "raw_refs": [{"field": field, "start": start, "end": len(text)}],
                    "confidence": 1.0,
                }
            )
        rows.append(
            {
                "source_record_id": record["source_record_id"],
                "claims": claims,
                "abstentions": [],
            }
        )
    return {"records": rows}


def verified(request):
    reviews = []
    for claim in request["payload"]["claims"]:
        ref = {
            "kind": "header",
            "field": claim["raw_refs"][0]["field"],
            "start": 0,
            "end": len(claim["raw_refs"][0]["field"]),
        }
        reviews.append(
            {
                "claim_id": claim["claim_id"],
                "verdict": "supported",
                "meaning_refs": [ref],
                "subject_refs": [ref],
                "reason": "Scripted source meaning and owner check",
            }
        )
    return {"reviews": reviews}


class ScriptedProvider:
    model_id, model_version, real_model = "scripted", "scripted", False

    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def generate(self, request, call_id):
        self.calls.append((copy.deepcopy(request), call_id))
        if self.callback:
            value = self.callback(request)
        elif request["purpose"] == "llm_extract":
            value = extracted(request)
        elif request["purpose"] == "llm_verify":
            value = verified(request)
        else:
            raise AssertionError("Unexpected scripted stage")
        check_response(value, request["output_schema"], "direct")
        return {
            "config": value,
            "usage": {},
            "cost_basis": "unknown",
            "real_model": False,
        }


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        (self.evidence / "data.csv").write_text(
            "Statement\nRegistered as: One Test Pty Ltd\nRegistered as: Two Test Pty Ltd\n"
        )
        (self.evidence / "source.txt").write_text(
            "Statement: Registered legal name of the company described by this record."
        )
        self.context_count = 0
        self.request = {
            "source_id": "invented",
            "source_name": "Invented fixture",
            "evidence_root": str(self.evidence),
            "ontology_path": str(
                Path(__file__).resolve().parents[1]
                / "schema"
                / "firmable_ontology.yaml"
            ),
            "state_dir": str(self.root / "state"),
            "trace_dir": str(self.root / "trace"),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def context(self, provider=None, config=None):
        request = {
            **self.request,
            "state_dir": str(self.root / ("state-" + str(self.context_count))),
        }
        self.context_count += 1
        runtime = Runtime(request, provider or ScriptedProvider())
        rows = runtime.tables[0]["records"]
        config = copy.deepcopy(config or task_config())
        config["source_schema"] = table_schema(runtime.tables, [0], runtime.paths)
        return runtime, config, rows

    def run_route(self, runtime, config, rows, metadata=None):
        return run_engine(
            config,
            rows,
            runtime.docs,
            metadata or {},
            "2026-10-13T00:00:00+00:00",
            runtime=runtime,
        )


class RouteTests(Fixture):
    def test_whole_field_refs_preserve_original_text_before_normalizing_value(self):
        (self.evidence / "data.csv").write_text(
            "Legal name\n  One  One Pty Ltd  \n  Ālpha  Test Pty Ltd  \n"
        )
        (self.evidence / "source.txt").write_text(
            "Legal name: Registered legal name of the company."
        )

        def reply(request):
            if request["purpose"] == "llm_extract":
                return {
                    "records": [
                        {
                            "source_record_id": record["source_record_id"],
                            "claims": [
                                {
                                    "canonical_field": "entity.legal_name",
                                    "value": " ".join(
                                        record["raw_record"]["Legal name"].split()
                                    ),
                                    "raw_refs": [
                                        {"field": "Legal name", "whole_field": True}
                                    ],
                                    "confidence": 1.0,
                                }
                            ],
                            "abstentions": [],
                        }
                        for record in request["payload"]["records"]
                    ]
                }
            value = verified(request)
            for review in value["reviews"]:
                review["meaning_refs"] = [
                    {"kind": "header", "field": "Legal name", "whole_field": True}
                ]
                review["subject_refs"] = [
                    {"kind": "raw", "field": "Legal name", "whole_field": True}
                ]
            return value

        runtime, config, rows = self.context(
            ScriptedProvider(reply), task_config([mapping("Legal name")])
        )
        observations, report = self.run_route(runtime, config, rows)
        self.assertEqual(report["errors"], [])
        for record, observation, claim, review in zip(
            rows, observations, report["accepted_claims"], report["semantic_support"]
        ):
            original = record["raw_record"]["Legal name"]
            resolved = claim["raw_refs"][0]
            self.assertEqual(
                resolved,
                {
                    "field": "Legal name",
                    "start": 0,
                    "end": len(original),
                    "quote": original,
                    "source_text_sha256": byte_hash(original.encode()),
                },
            )
            self.assertEqual(
                observation["entity"]["legal_name"], " ".join(original.split())
            )
            self.assertEqual(observation["raw_record"], record["raw_record"])
            self.assertEqual(
                review["resolved_evidence"]["subject_refs"][0]["quote"], original
            )
            self.assertEqual(
                review["resolved_evidence"]["meaning_refs"][0]["quote"], "Legal name"
            )
        self.assertEqual(self.run_route(runtime, config, rows), (observations, report))
        self.assertEqual(len(runtime.provider.calls), 2)

    def test_whole_field_response_extension_roundtrips_without_changing_old_refs(self):
        for ref in (
            {"field": "Statement", "whole_field": True},
            {"field": "Statement", "quote": "Original  text"},
            {"field": "Statement", "start": 0, "end": 14},
        ):
            claim = {
                "canonical_field": "entity.legal_name",
                "value": "Original text",
                "raw_refs": [ref],
                "confidence": 1.0,
            }
            extraction = {
                "records": [
                    {"source_record_id": "r", "claims": [claim], "abstentions": []}
                ]
            }
            review_ref = {"kind": "raw", **ref}
            review = {
                "reviews": [
                    {
                        "claim_id": "c",
                        "verdict": "supported",
                        "meaning_refs": [review_ref],
                        "subject_refs": [review_ref],
                        "reason": "Original record",
                    }
                ]
            }
            for schema, response in (
                (llm_route.extraction_schema(), extraction),
                (llm_route.VERIFY_SCHEMA, review),
            ):
                with self.subTest(ref=ref, stage=list(response)[0]):
                    contract = OpenRouterWireContract(schema)
                    self.assertEqual(
                        contract.decode(contract.encode(response)), response
                    )

    def test_whole_field_reference_forms_do_not_accept_mixed_or_document_spans(self):
        for extra in ({"quote": "x"}, {"start": 0}, {"end": 1}, {"whole_field": False}):
            ref = {"field": "Statement", "whole_field": True, **extra}
            with self.subTest(ref=ref), self.assertRaises(Failure):
                check_response(ref, llm_route.RAW_REF, "direct")
            with self.subTest(ref=ref), self.assertRaises(Failure):
                llm_route._resolve_span("x", ref)
        ref = {"kind": "documentation", "field": "Statement", "whole_field": True}
        with self.assertRaises(Failure):
            check_response(ref, llm_route.EVIDENCE_REF, "direct")
        with self.assertRaises(Failure):
            llm_route._resolve_span("Original documentation", ref)
        with self.assertRaises(Failure) as raised:
            llm_route._resolve_span(
                "Original  text", {"field": "Statement", "quote": "Original text"}
            )
        self.assertEqual(raised.exception.details["reason_code"], "quote_not_found")

    def test_whole_field_references_cannot_invent_or_borrow_a_value(self):
        claim = {
            "canonical_field": "entity.legal_name",
            "value": "Other Pty Ltd",
            "raw_refs": [{"field": "Statement", "whole_field": True}],
            "confidence": 1.0,
        }
        for raw in (
            {},
            {"Statement": None},
            {"Statement": ""},
            {"Statement": "   "},
            {"Statement": "Actual Pty Ltd"},
        ):
            with self.subTest(raw=raw), self.assertRaises(Failure):
                llm_route._check_claim(
                    claim, {"source_record_id": "r", "raw_record": raw}, mapping()
                )
        claim["raw_refs"][0]["field"] = "Other"
        with self.assertRaises(Failure) as raised:
            llm_route._check_claim(
                claim,
                {
                    "source_record_id": "r",
                    "raw_record": {
                        "Statement": "Actual Pty Ltd",
                        "Other": "Other Pty Ltd",
                    },
                },
                mapping(),
            )
        self.assertEqual(raised.exception.details["reason_code"], "foreign_field")

    def test_wrong_header_quote_is_rejected_without_becoming_a_raw_reference(self):
        def reply(request):
            if request["purpose"] == "llm_extract":
                return extracted(request)
            value = verified(request)
            for claim, review in zip(request["payload"]["claims"], value["reviews"]):
                review["meaning_refs"] = [
                    {"kind": "header", "field": "Statement", "quote": claim["value"]}
                ]
            return value

        runtime, config, rows = self.context(ScriptedProvider(reply))
        observations, report = self.run_route(runtime, config, rows)
        self.assertTrue(all("entity" not in row for row in observations))
        self.assertEqual(
            {e["reason_code"] for e in report["errors"]}, {"quote_not_found"}
        )
        self.assertEqual(
            {e["validation_stage"] for e in report["errors"]}, {"llm_review_evidence"}
        )
        self.assertEqual(
            {e["offending_ref"]["kind"] for e in report["errors"]}, {"header"}
        )

    def test_full_address_claim_cannot_crop_or_drop_configured_fields(self):
        item = mapping("Business address", "address.full")
        record = {
            "source_record_id": "r",
            "raw_record": {"Business address": "12  Example Street"},
        }
        claim = {
            "canonical_field": "address.full",
            "value": "12 Example Street",
            "confidence": 1.0,
            "raw_refs": [{"field": "Business address", "whole_field": True}],
        }
        self.assertEqual(
            llm_route._check_claim(claim, record, item)["value"], "12 Example Street"
        )
        claim.update(
            value="12", raw_refs=[{"field": "Business address", "quote": "12"}]
        )
        with self.assertRaises(Failure) as raised:
            llm_route._check_claim(claim, record, item)
        self.assertEqual(
            raised.exception.details["reason_code"], "address_complete_fields_required"
        )
        record["raw_record"]["Postcode"] = "2345"
        item["source_fields"].append("Postcode")
        claim.update(
            value="12 Example Street",
            raw_refs=[{"field": "Business address", "whole_field": True}],
        )
        with self.assertRaises(Failure) as raised:
            llm_route._check_claim(claim, record, item)
        self.assertEqual(
            raised.exception.details["reason_code"], "address_complete_fields_required"
        )
        claim["raw_refs"].append({"field": "Postcode", "whole_field": True})
        claim["value"] += " 2345"
        self.assertEqual(
            llm_route._check_claim(claim, record, item)["value"], claim["value"]
        )

    def test_llm_address_config_cannot_bypass_shared_source_semantics(self):
        for fields, docs in (
            (["Locality"], "Locality: Suburb for the company."),
            (
                ["Locality", "Postcode"],
                "Locality: Suburb for the company.\nPostcode: Postcode for the company.",
            ),
            (["Business address"], "Business address: Incomplete street address only."),
        ):
            with self.subTest(fields=fields):
                (self.evidence / "data.csv").write_text(
                    ",".join(fields) + "\n" + ",".join(["Example"] * len(fields)) + "\n"
                )
                (self.evidence / "source.txt").write_text(docs)
                item = mapping(fields[0], "address.full")
                item["source_fields"] = fields
                runtime, config, rows = self.context(config=task_config([item]))
                with self.assertRaises(Failure):
                    self.run_route(runtime, config, rows)
                self.assertEqual(runtime.provider.calls, [])

    def test_source_address_attribute_preserves_locality_text_and_its_owner(self):
        (self.evidence / "data.csv").write_text(
            "Applicant address,Agency address\n"
            "North  District NSW 2345,9 Civic Road\n"
            "South  District NSW 2346,9 Civic Road\n"
        )
        (self.evidence / "source.txt").write_text(
            "Applicant address: Address of the applicant business.\n"
            "Agency address: Address of the contracting agency."
        )
        config = task_config([mapping("Applicant address", "address.full")])
        config["unmapped_fields"] = [
            {"source_field": "Agency address", "reason": "Different subject"}
        ]
        config["execution"]["subject"] = "The applicant business"

        def reply(request):
            if request["purpose"] == "llm_extract":
                result = extracted(request)
                for row in result["records"]:
                    claim = row["claims"][0]
                    claim["value"] = " ".join(claim["value"].split())
                    claim["raw_refs"] = [
                        {"field": "Applicant address", "whole_field": True}
                    ]
                return result
            return verified(request)

        runtime, config, rows = self.context(ScriptedProvider(reply), config)
        observations, report = self.run_route(runtime, config, rows)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["accepted_claims"]), 2)
        self.assertEqual(
            [row["address"]["full"] for row in observations],
            ["North District NSW 2345", "South District NSW 2346"],
        )
        for claim, record in zip(report["accepted_claims"], rows):
            self.assertEqual(
                claim["raw_refs"][0]["quote"],
                record["raw_record"]["Applicant address"],
            )

    def test_quotes_are_host_resolved_and_hashed_in_raw_and_critic_receipts(self):
        def reply(request):
            if request["purpose"] == "llm_extract":
                result = extracted(request)
                for row in result["records"]:
                    claim = row["claims"][0]
                    claim["raw_refs"] = [
                        {"field": "Statement", "quote": claim["value"]}
                    ]
                return result
            result = verified(request)
            for review in result["reviews"]:
                ref = {
                    "kind": "documentation",
                    "field": "Statement",
                    "quote": request["payload"]["source_documentation"],
                }
                review.update(meaning_refs=[ref], subject_refs=[ref])
            return result

        runtime, config, rows = self.context(ScriptedProvider(reply))
        observations, report = self.run_route(runtime, config, rows)
        self.assertEqual(report["errors"], [])
        raw = report["accepted_claims"][0]["raw_refs"][0]
        self.assertEqual(raw["quote"], observations[0]["entity"]["legal_name"])
        self.assertEqual(
            rows[0]["raw_record"]["Statement"][raw["start"] : raw["end"]], raw["quote"]
        )
        self.assertEqual(len(raw["source_text_sha256"]), 64)
        evidence = report["semantic_support"][0]["resolved_evidence"]["meaning_refs"][0]
        self.assertEqual(evidence["quote"], runtime.docs)
        self.assertEqual(evidence["start"], 0)
        self.assertEqual(len(evidence["source_text_sha256"]), 64)

    def test_ambiguous_quote_needs_explicit_matching_offsets(self):
        with self.assertRaises(Failure) as raised:
            llm_route._resolve_span(
                "Acme Acme", {"field": "Statement", "quote": "Acme"}
            )
        self.assertEqual(raised.exception.details["reason_code"], "ambiguous_quote")
        ref = llm_route._resolve_span(
            "Acme Acme", {"field": "Statement", "quote": "Acme", "start": 5, "end": 9}
        )
        self.assertEqual(ref["start"], 5)
        with self.assertRaises(Failure):
            llm_route._resolve_span(
                "Acme Acme",
                {"field": "Statement", "quote": "Other", "start": 5, "end": 9},
            )

    def test_identifier_cannot_crop_long_number_or_reorder_digits_or_change_type(self):
        item = mapping(target="entity.abn")
        cases = [
            (
                "ABN 518247535569",
                [{"field": "Statement", "start": 4, "end": 15}],
                "identifier_repair_forbidden",
            ),
            (
                "51824753565",
                [
                    {"field": "Statement", "start": 0, "end": 9},
                    {"field": "Statement", "start": 10, "end": 11},
                    {"field": "Statement", "start": 9, "end": 10},
                ],
                "identifier_repair_forbidden",
            ),
            (
                "ACN 51824753556",
                [{"field": "Statement", "start": 4, "end": 15}],
                "identifier_type_conflict",
            ),
        ]
        for raw, refs, code in cases:
            with self.subTest(raw=raw):
                claim = {
                    "canonical_field": "entity.abn",
                    "value": "51824753556",
                    "confidence": 1.0,
                    "raw_refs": refs,
                }
                record = {"source_record_id": "r", "raw_record": {"Statement": raw}}
                with self.assertRaises(Failure) as raised:
                    llm_route._check_claim(claim, record, item)
                self.assertEqual(raised.exception.details["reason_code"], code)
        record = {
            "source_record_id": "r",
            "raw_record": {
                "Statement": "Business ABN: 51 824 753 556; legal name elsewhere"
            },
        }
        checked = llm_route._check_claim(
            {
                "canonical_field": "entity.abn",
                "value": "51824753556",
                "confidence": 1.0,
                "raw_refs": [{"field": "Statement", "quote": "51 824 753 556"}],
            },
            record,
            item,
        )
        self.assertEqual(checked["value"], "51824753556")

    def test_exact_spans_fresh_review_wire_format_and_cached_replay(self):
        runtime, config, rows = self.context()
        observations, report = self.run_route(
            runtime,
            config,
            rows,
            {"metadata_modified": "2026-10-12T03:00:00Z", "license_id": "CC-BY"},
        )
        self.assertEqual(observations[0]["entity"]["legal_name"], "One Test Pty Ltd")
        self.assertEqual(
            observations[0]["observed_at_basis"]["evidence_ref"],
            "catalogue_metadata.metadata_modified",
        )
        self.assertFalse(report["model_review_is_human_gold"])
        self.assertEqual(len(report["candidate_claims"]), 2)
        verifier = runtime.provider.calls[1][0]
        self.assertNotIn("task_config", verifier["payload"])
        self.assertTrue(
            all("confidence" not in claim for claim in verifier["payload"]["claims"])
        )
        self.assertNotIn("AST transforms", verifier["system"])
        again = self.run_route(
            runtime,
            config,
            rows,
            {"metadata_modified": "2026-10-12T03:00:00Z", "license_id": "CC-BY"},
        )
        self.assertEqual(again, (observations, report))
        self.assertEqual(len(runtime.provider.calls), 2)
        for observation in observations:
            validate_observation(observation)

    def test_bad_span_and_high_confidence_invented_value_never_reach_critic(self):
        for defect, expected in (
            ("span", "invalid_span"),
            ("value", "value_not_in_raw"),
        ):
            with self.subTest(defect=defect):

                def reply(request):
                    value = extracted(request)
                    for row in value["records"]:
                        if defect == "span":
                            row["claims"][0]["raw_refs"][0]["end"] = 99999
                        else:
                            row["claims"][0]["value"] = "Invented Pty Ltd"
                    return value

                runtime, config, rows = self.context(ScriptedProvider(reply))
                observations, report = self.run_route(runtime, config, rows)
                self.assertTrue(all("entity" not in o for o in observations))
                self.assertEqual(
                    {e["reason_code"] for e in report["errors"]}, {expected}
                )
                self.assertEqual(len(runtime.provider.calls), 1)
                self.assertEqual(len(report["rejected_claims"]), 2)
                self.assertTrue(all(r["omissions"] for r in report["row_omissions"]))
                # Each subcase gets a separate durable run.

    def test_foreign_owner_cannot_be_overridden_by_supported_critic(self):
        (self.evidence / "data.csv").write_text(
            "Director name\nJane Smith\nJoe Jones\n"
        )
        (self.evidence / "source.txt").write_text(
            "Director name: Legal name of the director, a person."
        )
        runtime, config, rows = self.context(
            config=task_config([mapping("Director name")])
        )
        with self.assertRaises(Failure) as raised:
            self.run_route(runtime, config, rows)
        self.assertEqual(raised.exception.details["reason_code"], "foreign_subject")
        self.assertEqual(runtime.provider.calls, [])

    def test_supported_without_real_references_does_not_authorize_claim(self):
        def reply(request):
            if request["purpose"] == "llm_extract":
                return extracted(request)
            result = verified(request)
            for review in result["reviews"]:
                review["meaning_refs"] = []
            return result

        runtime, config, rows = self.context(ScriptedProvider(reply))
        observations, report = self.run_route(runtime, config, rows)
        self.assertTrue(all("entity" not in o for o in observations))
        self.assertEqual(
            {e["reason_code"] for e in report["errors"]}, {"missing_review_evidence"}
        )

    def test_invented_critic_document_span_is_host_error(self):
        def reply(request):
            if request["purpose"] == "llm_extract":
                return extracted(request)
            result = verified(request)
            for review in result["reviews"]:
                review["meaning_refs"][0].update(kind="documentation", end=999999)
            return result

        runtime, config, rows = self.context(ScriptedProvider(reply))
        observations, report = self.run_route(runtime, config, rows)
        self.assertTrue(all("entity" not in o for o in observations))
        self.assertEqual({e["reason_code"] for e in report["errors"]}, {"invalid_span"})

    def test_invalid_checksum_is_host_error_before_model_semantics(self):
        (self.evidence / "data.csv").write_text(
            "Statement\nABN: 61000000003\nABN: 61000000003\n"
        )
        runtime, config, rows = self.context(
            config=task_config([mapping(target="entity.abn")])
        )
        observations, report = self.run_route(runtime, config, rows)
        self.assertTrue(all("entity" not in o for o in observations))
        self.assertEqual(
            {e["reason_code"] for e in report["errors"]}, {"canonical_value_invalid"}
        )
        self.assertEqual(len(runtime.provider.calls), 1)

    def test_street_number_or_year_is_not_postcode_on_fresh_semantic_review(self):
        for text in ("Street number: 1234", "Reporting year: 2024"):
            with self.subTest(text=text):
                (self.evidence / "data.csv").write_text(
                    "Statement\n" + text + "\n" + text + "\n"
                )
                (self.evidence / "source.txt").write_text(
                    "Statement: Original description for this company."
                )

                def reply(request):
                    if request["purpose"] == "llm_extract":
                        return extracted(request)
                    value = verified(request)
                    for review in value["reviews"]:
                        review.update(
                            verdict="contradicted",
                            reason="Source labels a street number/year, not postcode",
                        )
                    return value

                runtime, config, rows = self.context(
                    ScriptedProvider(reply),
                    task_config([mapping(target="address.postcode")]),
                )
                observations, report = self.run_route(runtime, config, rows)
                self.assertTrue(all("address" not in o for o in observations))
                self.assertEqual(
                    {e["reason_code"] for e in report["errors"]},
                    {"model_semantic_contradicted"},
                )
                self.assertEqual(len(runtime.provider.calls), 2)

    def test_uncertain_claim_is_omitted_while_supported_claim_remains(self):
        (self.evidence / "data.csv").write_text(
            "Statement,Alias\nRegistered as: One Test Pty Ltd,Trade: One\nRegistered as: Two Test Pty Ltd,Trade: Two\n"
        )
        (self.evidence / "source.txt").write_text(
            "Statement: Registered legal name of the company.\nAlias: A name with uncertain meaning."
        )

        def reply(request):
            if request["purpose"] == "llm_extract":
                return extracted(request)
            value = verified(request)
            for claim, review in zip(request["payload"]["claims"], value["reviews"]):
                if claim["canonical_field"] == "entity.trading_name":
                    review.update(
                        verdict="uncertain",
                        reason="Source does not establish trading-name meaning",
                    )
            return value

        runtime, config, rows = self.context(
            ScriptedProvider(reply),
            task_config([mapping(), mapping("Alias", "entity.trading_name")]),
        )
        observations, report = self.run_route(runtime, config, rows)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["uncertain_claims"]), 2)
        self.assertTrue(
            all(
                o["entity"].get("legal_name") and "trading_name" not in o["entity"]
                for o in observations
            )
        )
        self.assertTrue(
            all(
                "model_semantic_uncertain" in str(o["validation_notes"])
                for o in observations
            )
        )

    def test_unknown_verifier_is_not_resent_and_extractor_remains_cached(self):
        def reply(request):
            if request["purpose"] == "llm_verify":
                raise Failure(
                    "model_error", "Scripted ambiguous dispatched verifier outcome"
                )
            return extracted(request)

        runtime, config, rows = self.context(ScriptedProvider(reply))
        with self.assertRaises(Failure):
            self.run_route(runtime, config, rows)
        self.assertEqual(len(runtime.provider.calls), 2)
        with self.assertRaises(Failure):
            self.run_route(runtime, config, rows)
        self.assertEqual(len(runtime.provider.calls), 2)
        self.assertEqual(
            runtime.run.state["calls"][-1]["status"], "model_outcome_unknown"
        )

    def test_malformed_task_is_local_feedback_but_provider_schema_failure_terminates(
        self,
    ):
        runtime, config, rows = self.context()
        bad = copy.deepcopy(config)
        del bad["execution"]["subject"]
        with self.assertRaises(Failure) as raised:
            self.run_route(runtime, bad, rows)
        self.assertEqual(raised.exception.kind, "validation_error")
        runtime.provider.callback = lambda request: {"records": [], "fabricated": True}
        with self.assertRaises(Failure):
            self.run_route(runtime, config, rows)
        self.assertEqual(
            runtime.run.state["calls"][-1]["status"], "model_outcome_unknown"
        )
        self.assertEqual(len(runtime.provider.calls), 1)

    def test_approved_scope_reaches_extractor_verifier_and_result_cache(self):
        self.request["execution_scope"] = "approved_extraction"
        runtime, config, rows = self.context()
        result = self.run_route(runtime, config, rows)
        self.assertEqual(result[1]["execution_scope"], "approved_extraction")
        self.assertEqual(
            [call[0]["payload"]["execution_scope"] for call in runtime.provider.calls],
            ["approved_extraction", "approved_extraction"],
        )
        self.assertEqual(self.run_route(runtime, config, rows), result)
        self.assertEqual(len(runtime.provider.calls), 2)
