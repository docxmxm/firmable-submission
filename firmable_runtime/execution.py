"""Config execution and observation/profile format boundaries."""

import copy
import re
from datetime import datetime
from pathlib import Path

from .common import Failure, safe_path
from .engine import (
    CANONICAL_FIELDS,
    canonical_valid,
    ENUMS,
    CompileDiagnostics,
    ast_nodes,
    check_utc_assumptions,
    compile_ast,
    compile_mappings,
    eval_ast,
    extract_records,
)
from .native import input_manifest, public_structure, read_native
from .schema import CONFIG_SCHEMA, check_response

UNKNOWN_TARGETS = {field for field, values in ENUMS.items() if "unknown" in values}


def _fail(message):
    raise Failure("validation_error", message)


def _aware_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise Failure(
            "validation_error", "Approval/generation time must be timezone ISO8601"
        ) from error
    if parsed.tzinfo is None:
        _fail("Approval/generation time needs a timezone")
    return parsed


def record_ref(record):
    try:
        result = (record["source_id"], record["source_record_id"])
    except (KeyError, TypeError):
        _fail("Missing native record identity")
    if not all(isinstance(value, str) and value for value in result):
        _fail("Record identity must contain nonempty strings")
    return result


def _probability(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def validate_observation(observation):
    """Check hard envelope/ontology errors only; cannot decide semantic support."""
    record_ref(observation)
    for key in ("observed_at", "ingested_at"):
        _aware_time(observation.get(key))
    for key in ("licence", "extractor_version"):
        if not isinstance(observation.get(key), str) or not observation[key].strip():
            _fail("Missing observation envelope: " + key)
    if not isinstance(observation.get("raw_record"), dict):
        _fail("raw_record must be an object")
    basis = observation.get("observed_at_basis", {})
    if (
        basis.get("kind")
        not in {"record_stated", "dataset_updated_proxy", "ingested_at_proxy"}
        or not isinstance(basis.get("evidence_ref"), str)
        or not basis["evidence_ref"].strip()
    ):
        _fail("Missing observed_at evidence basis")
    if (
        basis["kind"] == "ingested_at_proxy"
        and not str(basis.get("limitation", "")).strip()
    ):
        _fail("Ingest proxy needs an explicit limitation")
    notes = observation.get("validation_notes", [])
    if observation["licence"] == "unknown" and (
        not isinstance(notes, list)
        or not notes
        or not all(isinstance(note, str) and note.strip() for note in notes)
    ):
        _fail("Unknown licence needs validation_notes explaining missing evidence")
    confidence = observation.get("confidence", {})
    if (
        set(confidence) != {"source_reliability", "field_confidence"}
        or not _probability(confidence["source_reliability"])
        or not isinstance(confidence["field_confidence"], dict)
    ):
        _fail("Missing or blended observation confidence")
    fields = confidence["field_confidence"]
    if any(
        field not in CANONICAL_FIELDS or not _probability(value)
        for field, value in fields.items()
    ):
        _fail("Invalid field confidence")
    for group in ("entity", "address"):
        for name, value in observation.get(group, {}).items():
            field = group + "." + name
            if not canonical_valid(field, value):
                _fail("Invalid canonical field, type or value: " + field)
            if field not in fields:
                _fail("Populated canonical field has no confidence")
    return True


def mapping_accounting(mappings, emitted_values):
    """Classify public claim kinds, not mapping correctness or source support."""
    counts = {"informative": 0, "unknown_abstentions": 0}
    for mapping in mappings:
        field = mapping.get("canonical_field")
        if field not in CANONICAL_FIELDS:
            _fail("Invalid canonical mapping target")
        kind = mapping.get("claim_kind", "informative")
        if kind == "unknown_abstention":
            if field not in UNKNOWN_TARGETS or any(
                value != "unknown" for value in emitted_values.get(field, [])
            ):
                _fail("unknown_abstention must use a permitted unknown enum only")
            counts["unknown_abstentions"] += 1
        elif kind == "informative":
            counts["informative"] += 1
        else:
            _fail("Invalid claim_kind")
    return counts


def pointer_get(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise Failure("validation_error", "Invalid JSON Pointer")
    try:
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise Failure("validation_error", "JSON Pointer does not resolve") from exc


def discover(evidence_root, source_id):
    manifest = input_manifest(evidence_root)
    paths = [
        item["path"]
        for item in manifest
        if Path(item["path"]).suffix.lower() in {".xlsx", ".csv", ".tsv", ".json"}
    ]
    workbooks = [path for path in paths if Path(path).suffix.lower() == ".xlsx"]
    paths = workbooks or paths
    if not paths:
        raise Failure("no_data", "No supported native source file was found")
    tables = []
    for path in paths:
        tables.extend(read_native(evidence_root, path, source_id))
    documentation = "\n".join(
        safe_path(evidence_root, item["path"]).read_text(encoding="utf-8")
        for item in manifest
        if Path(item["path"]).suffix.lower() in {".md", ".txt"}
    )
    # PDFs are documentation, never business-record tables. A retained text
    # rendition avoids duplication; the agent tool can still read original pages.
    text_stems = {
        str(Path(item["path"]).with_suffix(""))
        for item in manifest
        if Path(item["path"]).suffix.lower() in {".txt", ".md"}
    }
    pdf_only = [
        item
        for item in manifest
        if Path(item["path"]).suffix.lower() == ".pdf"
        and str(Path(item["path"]).with_suffix("")) not in text_stems
    ]
    if pdf_only:
        from .source_document_tools import document_pages

        pages = document_pages(evidence_root, pdf_only)
        documentation += "\n" + "\n".join(
            page["text"] for page in pages["pages"] if not page["ocr_required"]
        )
    return tables, paths, manifest, documentation


def table_schema(tables, selected, data_files):
    chosen = [tables[index] for index in selected]
    first = chosen[0]
    formats = {table["format"] for table in chosen}
    return {
        "format": ("excel" if first["format"] == "xlsx" else first["format"])
        if len(formats) == 1
        else "other",
        "fields": [
            {"source_field": field, "inferred_type": "native_text_or_json"}
            for field in sorted(set().union(*(set(t["fields"]) for t in chosen)))
        ],
        "data_files": data_files,
        "discovery_method": "native_structure_then_runtime_model_table_selection",
        "discovered_data_sheet": first.get("sheet"),
        "header_row_index": first.get("header_row", 1) - 1,
        "record_path": first.get("record_pointer", ""),
        "selected_table_ids": selected,
        "reader_specs": public_structure(chosen),
    }


def compile_config(config, documentation, *, name_group_scope=None, name_review=None):
    from .schema import SOURCE_SCHEMA

    if name_group_scope is not None:
        name_group_scope.clear()
    if not isinstance(config, dict):
        raise Failure("validation_error", "Configuration must be an object")
    check_response(
        {key: value for key, value in config.items() if key != "source_schema"},
        CONFIG_SCHEMA,
    )
    check_response(config.get("source_schema"), SOURCE_SCHEMA)
    reviewed_name_indices = ()
    if name_review is not None:
        from .config_audit import validated_name_review

        reviewed_name_indices = validated_name_review(config, documentation, name_review)
    diagnostics = CompileDiagnostics()
    schema = config.get("source_schema", {}) if isinstance(config, dict) else {}
    native_fields = schema.get("fields", []) if isinstance(schema, dict) else []
    if not isinstance(native_fields, list) or any(
        not isinstance(f, dict) or not isinstance(f.get("source_field"), str)
        for f in native_fields
    ):
        diagnostics.add(
            "native_inventory",
            "Native field inventory is malformed",
            "/source_schema/fields",
        )
        native_fields = []
    inventory = [field["source_field"] for field in native_fields]
    raw_mappings = config.get("field_mappings") if isinstance(config, dict) else None
    mappings = [] if isinstance(raw_mappings, list) else raw_mappings
    if isinstance(raw_mappings, list):
        for index, value in enumerate(raw_mappings[:80]):
            mapping = copy.deepcopy(value)
            if isinstance(mapping, dict):
                at = "/field_mappings/" + str(index)
                for name in (
                    "source_field",
                    "source_fields",
                    "canonical_field",
                    "confidence",
                    "transformation",
                    "semantic_evidence",
                    "execution_pointer",
                ):
                    if name not in mapping:
                        diagnostics.add(
                            "missing_mapping_field",
                            "Required mapping field is missing: " + name,
                            at + "/" + name,
                            index,
                        )
                try:
                    mapping["ast"] = pointer_get(
                        config, mapping.get("execution_pointer")
                    )
                except Failure as exc:
                    diagnostics.add(
                        "execution_pointer", str(exc), at + "/execution_pointer", index
                    )
                    mapping["ast"] = None
            mappings.append(mapping)
        if len(raw_mappings) > 80:
            diagnostics.add(
                "mapping_bound", "Mapping list exceeds 80", "/field_mappings"
            )
    execution = config.get("execution", {}) if isinstance(config, dict) else {}
    if not isinstance(execution, dict):
        diagnostics.add("execution_shape", "execution must be an object", "/execution")
        execution = {}
    metadata_fields = set()
    for name in ("observed_time", "valid_from", "valid_to"):
        spec = execution.get(name)
        if spec is not None:
            at = "/execution/" + name
            if (
                not isinstance(spec, dict)
                or not isinstance(spec.get("source_fields"), list)
                or not all(isinstance(f, str) for f in spec["source_fields"])
            ):
                diagnostics.add(
                    "time_spec",
                    "Time metadata requires ast and declared source_fields",
                    at,
                )
                continue
            start = diagnostics.finding_count
            compile_ast(
                spec.get("ast"),
                inventory,
                spec["source_fields"],
                diagnostics=diagnostics,
                pointer=at + "/ast",
            )
            if spec.get("evidence_ref") not in spec["source_fields"]:
                diagnostics.add(
                    "time_evidence",
                    "Time evidence must reference an actual consumed field",
                    at + "/evidence_ref",
                )
            if diagnostics.finding_count == start:
                check_utc_assumptions(
                    spec["ast"],
                    spec.get("evidence_ref"),
                    documentation,
                    inventory,
                    diagnostics,
                    pointer=at + "/ast",
                    evidence_field=spec.get("evidence_ref"),
                )
            if diagnostics.finding_count == start and name in {
                "valid_from",
                "valid_to",
            }:
                from .semantic_support import observation_validity_support

                output_fields = {
                    node["name"]
                    for path, node in ast_nodes(spec["ast"])
                    if node["op"] == "field" and "/condition/" not in path + "/"
                }
                if spec["evidence_ref"] not in output_fields:
                    diagnostics.add(
                        "validity_ownership",
                        "Validity evidence_ref must identify a value-producing date field, not only a predicate.",
                        at,
                    )
                for field in sorted(output_fields):
                    support = observation_validity_support(
                        field, documentation, inventory, name
                    )
                    if not support["supported"]:
                        diagnostics.add(
                            "validity_ownership",
                            support["reason"],
                            at,
                            semantic_support=support,
                        )
            if diagnostics.finding_count == start:
                metadata_fields.update(spec["source_fields"])
    compiled = compile_mappings(
        mappings,
        inventory,
        documentation,
        config.get("unmapped_fields", []) if isinstance(config, dict) else [],
        diagnostics=diagnostics,
        metadata_fields=metadata_fields,
        name_group_scope=name_group_scope,
        reviewed_name_indices=reviewed_name_indices,
    )
    diagnostics.finish()
    return compiled


def _dataset_time(metadata, docs):

    candidates = [
        (metadata.get(key), "catalogue_metadata." + key)
        for key in ("metadata_modified", "last_updated", "updated")
    ]

    for match in re.finditer(
        r"(?im)^\*{0,2}Last Updated:\*{0,2}\s*([^\r\n]+?)\s*$", docs
    ):
        candidates.append((match[1].strip(), "source_documentation:Last Updated"))
    for value, ref in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.isoformat(), ref
        except ValueError:
            pass
    return None, None


def _metadata(metadata, docs):
    licence = (
        metadata.get("licence")
        or metadata.get("license_title")
        or metadata.get("license_id")
        or "unknown"
    )
    updated, _ = _dataset_time(metadata, docs)
    # Documentary metadata is source evidence; source-independent label parsing.

    if licence == "unknown":
        match = re.search(r"(?im)^\*{0,2}Licen[cs]e:\*{0,2}\s*(.+?)\s*$", docs)
        if match:
            licence = match[1].strip()
    return str(licence), updated


def wire_observations(internal):
    result = []
    for item in internal:
        base = {
            key: item[key]
            for key in (
                "source_id",
                "source_record_id",
                "raw_record",
                "observed_at",
                "ingested_at",
                "licence",
                "extractor_version",
            )
        }
        basis = (
            "record_stated"
            if item["observed_at_basis"] == "source_stated"
            else item["observed_at_basis"]
        )
        base["observed_at_basis"] = {
            "kind": basis,
            "evidence_ref": item["observed_at_evidence_ref"],
        }
        if basis != "record_stated":
            base["observed_at_basis"]["limitation"] = (
                "; ".join(item["limitations"])
                or "Proxy time does not establish source claim time"
            )
        base["validation_notes"] = item["limitations"] + [
            o["canonical_field"] + ": " + o["reason"] for o in item["omissions"]
        ]
        base["confidence"] = {
            "source_reliability": item["source_reliability"],
            "field_confidence": copy.deepcopy(item["field_confidence"]),
        }
        if item.get("locator"):
            base["native_locator"] = item["locator"]
        names = item["values"].get("entity.trading_name", []) or [None]
        for trading in names:
            observation = copy.deepcopy(base)
            for target, value in item["values"].items():
                group, field = target.split(".")
                observation.setdefault(group, {})[field] = (
                    trading if target == "entity.trading_name" else value
                )
            validate_observation(observation)
            result.append(observation)
    return result


def run_engine(
    config, records, documentation, metadata, timestamp, *, runtime=None, name_review=None
):
    from .llm_route import is_llm, execute

    if is_llm(config):
        return execute(config, records, documentation, metadata, timestamp, runtime)
    compiled = compile_config(config, documentation, name_review=name_review)
    licence, updated = _metadata(metadata, documentation)
    internal, report = extract_records(
        records,
        compiled,
        licence=licence,
        observed_time=config.get("execution", {}).get("observed_time"),
        dataset_updated=updated,
        ingested_at=timestamp,
    )
    for item in internal:
        if item["observed_at_basis"] == "dataset_updated_proxy":
            item["observed_at_evidence_ref"] = _dataset_time(metadata, documentation)[1]
    observations = wire_observations(internal)
    for observation in observations:
        for name in ("valid_from", "valid_to"):
            spec = config.get("execution", {}).get(name)
            if spec:
                value = eval_ast(spec["ast"], observation["raw_record"])
                if value is not None:
                    observation[name] = value
    values = {}
    for observation in observations:
        for group in ("entity", "address"):
            for field, value in observation.get(group, {}).items():
                values.setdefault(group + "." + field, []).append(value)
    mapping_accounting(config["field_mappings"], values)
    return observations, report


def internal_observations(observations):
    result = []
    for item in observations:
        validate_observation(item)
        values = {
            group + "." + field: value
            for group in ("entity", "address")
            for field, value in item.get(group, {}).items()
        }
        result.append(
            {
                **copy.deepcopy(item),
                "values": values,
                "source_reliability": item["confidence"]["source_reliability"],
                "field_confidence": item["confidence"]["field_confidence"],
                "observed_at_basis": "source_stated"
                if item["observed_at_basis"]["kind"] == "record_stated"
                else item["observed_at_basis"]["kind"],
            }
        )
    return result


def wire_profiles(profiles):
    result = []
    for profile in profiles:
        output = {
            "entity_key": profile["entity_key"],
            "entity": {},
            "source_count": profile["source_count"],
            "field_coverage": len(profile["fields"]) / len(CANONICAL_FIELDS),
        }
        for target, field in profile["fields"].items():
            group, name = target.split(".")
            output.setdefault(group, {})[name] = {
                **field,
                "provenance": [
                    {**p, "observed_value": p["value"]} for p in field["provenance"]
                ],
            }
        result.append(output)
    return result
