"""Bounded, read-only field evidence from documents and exposed proposal rows."""

import copy
import re

from .common import Failure, byte_hash, canonical
from .native import field_state

MAX_FIELD_CHARS = 256
MAX_VALUE_EXAMPLES = 2
MAX_VALUE_BYTES = 512
MAX_DOCUMENT_SPANS = 3
MAX_SPAN_BYTES = 768
MAX_DOCUMENT_BYTES = 1536


def _definition_windows(documentation, field, inventory):
    from .semantic_support import field_contexts

    _, contexts = field_contexts(
        {"source_field": field, "source_fields": [field], "semantic_evidence": field},
        documentation,
        inventory,
    )
    original_lines = documentation.splitlines(keepends=True)
    contents = documentation.splitlines()
    starts, offset = [], 0
    for line in original_lines:
        starts.append(offset)
        offset += len(line)
    windows, unreliable = [], 0
    for context in contexts:
        if context.get("kind") != "document_field_definition":
            continue
        first, last = context.get("line"), context.get("end_line")
        if (
            type(first) is not int
            or type(last) is not int
            or not 1 <= first <= last <= len(contents)
        ):
            unreliable += 1
            continue
        start, end = starts[first - 1], starts[last - 1] + len(contents[last - 1])
        # field_contexts normalizes line endings. Bind offsets only after its
        # complete original lines agree, preserving CRLF and Unicode separators.
        if (
            documentation[start:end].splitlines()
            != context.get("text", "").splitlines()
        ):
            unreliable += 1
            continue
        windows.append(
            (
                start,
                end,
                {
                    "kind": "recognized_definition_context",
                    "field_binding": {
                        "source_field": field,
                        "basis": "existing_field_contexts",
                        "line": first,
                        "end_line": last,
                        "whole_span_exclusively_owned": False,
                    },
                },
            )
        )
    return windows, unreliable


def _document_spans(documentation, field, inventory=()):
    # ASCII word boundaries prevent Name from matching UserName. Non-ASCII
    # names may occur in prose without spaces; these remain literal mentions,
    # never semantic definitions.
    pattern = (
        (r"(?<!\w)" if field[0].isascii() and re.match(r"\w", field[0]) else "")
        + re.escape(field)
        + (r"(?!\w)" if field[-1].isascii() and re.match(r"\w", field[-1]) else "")
    )
    spans, seen, matches, windows, total_bytes = [], None, 0, 0, 0
    literal_windows = []
    omitted = {"oversize_span": 0, "span_limit": 0, "total_byte_limit": 0}
    for match in re.finditer(pattern, documentation):
        matches += 1
        start = documentation.rfind("\n", 0, match.start()) + 1
        line_end = documentation.find("\n", match.end())
        # Preserve complete lines, including a following line for definitions
        # written below a header. Oversize windows are omitted, not shortened.
        end = documentation.find("\n", line_end + 1) if line_end != -1 else -1
        end = len(documentation) if end == -1 else end
        window = (start, end)
        if window == seen:
            continue
        seen = window
        windows += 1
        literal_windows.append(
            (
                start,
                end,
                {
                    "kind": "literal_mention_only",
                    "match_start": match.start(),
                    "match_end": match.end(),
                },
            )
        )
    definitions, unreliable = _definition_windows(documentation, field, inventory)
    # Retain an existing bounded literal window when it already contains the
    # complete definition. Extra original context remains untrusted and is not
    # claimed as exclusively owned by the field.
    definitions = [
        (
            start,
            next(
                (
                    literal_end
                    for literal_start, literal_end, _ in literal_windows
                    if literal_start == start
                    and literal_end >= end
                    and len(documentation[start:literal_end].encode("utf-8"))
                    <= MAX_SPAN_BYTES
                ),
                end,
            ),
            metadata,
        )
        for start, end, metadata in definitions
    ]
    omitted_definitions = {**omitted, "unreliable_offsets": unreliable}
    selected, covered_literals, returned_definitions = set(), 0, 0
    for start, end, metadata in definitions + literal_windows:
        recognized = metadata["kind"] == "recognized_definition_context"
        if (start, end) in selected:
            continue
        if not recognized and any(
            item["start"] <= start and end <= item["end"]
            for item in spans
            if item["kind"] == "recognized_definition_context"
        ):
            covered_literals += 1
            continue
        quote = documentation[start:end]
        encoded = quote.encode("utf-8")
        counts = omitted_definitions if recognized else omitted
        if len(encoded) > MAX_SPAN_BYTES:
            counts["oversize_span"] += 1
        elif len(spans) >= MAX_DOCUMENT_SPANS:
            counts["span_limit"] += 1
        elif total_bytes + len(encoded) > MAX_DOCUMENT_BYTES:
            counts["total_byte_limit"] += 1
        else:
            if recognized:
                match = re.search(pattern, quote)
                if match:
                    metadata.update(
                        match_start=start + match.start(), match_end=start + match.end()
                    )
                returned_definitions += 1
            spans.append(
                {
                    "start": start,
                    "end": end,
                    "text": quote,
                    "sha256": byte_hash(encoded),
                    "utf8_bytes": len(encoded),
                    **metadata,
                }
            )
            selected.add((start, end))
            total_bytes += len(encoded)
    return {
        "document_sha256": byte_hash(documentation.encode("utf-8")),
        "offset_unit": "unicode_codepoints",
        "literal_match_count": matches,
        "matching_window_count": windows,
        "returned_span_bytes": total_bytes,
        "spans": spans,
        "omitted_windows": omitted,
        "recognized_definition_context_count": len(definitions),
        "returned_definition_context_count": returned_definitions,
        "definition_binding_status": "parser_bound_contexts"
        if definitions
        else "unreliable_original_offsets"
        if unreliable
        else "no_parser_bound_context",
        "omitted_definition_contexts": omitted_definitions,
        "literal_windows_covered_by_definition": covered_literals,
        "selection": "Complete existing-parser definition contexts first; remaining literal line-plus-next-line windows second. Over-budget spans are omitted, never shortened.",
        "binding_limitation": "A recognized context locates a field definition, not semantic support. Its original span can contain adjacent text; unchanged semantic checks determine ownership and meaning.",
        "semantic_support": "not_assessed",
    }


def inspect_field_evidence(runtime, source_schema, field):
    """Return evidence only; callers own quotas, artifacts and model decisions.

    This function never reads runtime tables, validation, reserve or file paths.
    An observed value or literal document mention does not establish its meaning.
    """
    fields = source_schema.get("fields", []) if isinstance(source_schema, dict) else []
    inventory = {
        item.get("source_field")
        for item in fields
        if isinstance(item, dict) and isinstance(item.get("source_field"), str)
    }
    if (
        not isinstance(field, str)
        or not field
        or len(field) > MAX_FIELD_CHARS
        or "\x00" in field
        or field not in inventory
    ):
        raise Failure(
            "validation_error", "Field evidence requires one exact bounded native field"
        )
    documentation = runtime.docs
    records = runtime.proposal_records()
    if not isinstance(documentation, str) or not isinstance(records, list):
        raise Failure(
            "validation_error",
            "Field evidence requires source text and exposed proposal records",
        )
    counts = {key: 0 for key in ("missing", "null", "blank", "present")}
    examples = []
    omitted = {"oversize_value": 0, "example_limit": 0, "non_json_value": 0}
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("raw_record"), dict
        ):
            raise Failure("validation_error", "Malformed exposed proposal record")
        reference = {key: record.get(key) for key in ("source_id", "source_record_id")}
        if any(
            not isinstance(value, str) or not value or len(value) > 256
            for value in reference.values()
        ):
            raise Failure(
                "validation_error", "Malformed exposed proposal record reference"
            )
        resolved = field_state(record["raw_record"], field)
        counts[resolved["state"]] += 1
        if resolved["state"] != "present":
            continue
        value = resolved["value"]
        try:
            encoded = canonical(value)
        except (TypeError, ValueError, RecursionError):
            omitted["non_json_value"] += 1
            continue
        if len(encoded) > MAX_VALUE_BYTES:
            omitted["oversize_value"] += 1
        elif len(examples) >= MAX_VALUE_EXAMPLES:
            omitted["example_limit"] += 1
        else:
            examples.append(
                {
                    "record_ref": reference,
                    "value": copy.deepcopy(value),
                    "value_sha256": byte_hash(encoded),
                    "value_json_utf8_bytes": len(encoded),
                }
            )
    document_pages = None
    if any(
        item["path"].lower().endswith((".pdf", ".md", ".txt"))
        for item in runtime.evidence_manifest
    ):
        from .source_document_tools import inspect_document_context

        document_pages = inspect_document_context(
            runtime.input["evidence_root"], runtime.evidence_manifest, field
        )
    return {
        "version": "bounded-field-evidence-2",
        "source_field": field,
        "scope": "source_documentation_and_already_exposed_proposal_only",
        "proposal_record_count": len(records),
        "presence_counts": counts,
        "value_examples": examples,
        "omitted_value_examples": omitted,
        "document_evidence": _document_spans(documentation, field, inventory),
        "original_document_pages": document_pages,
        "limits": {
            "field_chars": MAX_FIELD_CHARS,
            "value_examples": MAX_VALUE_EXAMPLES,
            "value_json_utf8_bytes": MAX_VALUE_BYTES,
            "document_spans": MAX_DOCUMENT_SPANS,
            "span_utf8_bytes": MAX_SPAN_BYTES,
            "total_span_utf8_bytes": MAX_DOCUMENT_BYTES,
        },
        "limitations": [
            "Literal field mentions and observed values are not same-field semantic definition support.",
            "Absence in these documents or exposed rows does not establish source-wide absence.",
            "No validation rows, reserve rows, external lookup, mapping approval or semantic judgment.",
        ],
    }
