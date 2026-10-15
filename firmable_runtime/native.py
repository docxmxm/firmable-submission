"""Bounded native input readers and immutable source field access.

A table heuristic proposes structure only; business meaning stays with the agent
and compiler. Raw cells and physical locators remain attached to every record.
"""

from __future__ import annotations

import copy
import csv
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .common import Failure, byte_hash, digest, safe_path, strict_json

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
MAX_CELLS = 1_000_000
MAX_CELL_CHARS = 100_000
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def input_manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Failure("validation_error", "Invalid evidence root")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Failure("validation_error", "Symlink evidence is unsupported")
        if path.is_file():
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": byte_hash(path.read_bytes()),
                }
            )
    return result


def decode_text(data):
    for encoding in (
        "utf-8-sig",
        "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8",
    ):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            pass
    raise Failure(
        "unsupported_format", "Only UTF-8 and BOM-marked UTF-16 text is supported"
    )


def leaf_fields(value, prefix=""):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            # The public inventory exposes nested dictionary paths with dots.
            path = (prefix + "." if prefix else "") + key
            if isinstance(item, dict):
                result.update(leaf_fields(item, path))
            else:
                result.add(path)
    return result


def field_state(raw, field):
    """Shared bounded path lookup; missing is distinct from present null/blank."""
    if (
        not isinstance(raw, dict)
        or not isinstance(field, str)
        or not field
        or "\x00" in field
    ):
        raise Failure("validation_error", "Invalid source field/path")
    if field in raw:
        value = raw[field]
    else:
        if field.startswith("/"):
            if re.search(r"~(?![01])", field):
                raise Failure(
                    "validation_error", "Invalid JSON Pointer escape in source field"
                )
            parts = [
                part.replace("~1", "/").replace("~0", "~")
                for part in field[1:].split("/")
            ]
        else:
            parts = field.split(".")
        if any(not part for part in parts):
            raise Failure("validation_error", "Empty source path component")
        value = raw
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                return {"present": False, "state": "missing", "value": None}
            value = value[part]
    state = (
        "null"
        if value is None
        else "blank"
        if isinstance(value, str) and not value.strip()
        else "present"
    )
    return {"present": True, "state": state, "value": value}


def field_value(raw, name, default=None):
    state = field_state(raw, name)
    return state["value"] if state["present"] else default


def is_column_reference(field):
    """Reserved structural keys carry position, never invented header meaning."""
    return (
        isinstance(field, str)
        and re.fullmatch(r"@*native-column:[0-9a-f]{64}", field) is not None
    )


def _record(source_id, path, file_sha, locator, raw):
    ident = {"file": path, "snapshot": file_sha, "locator": locator}
    return {
        "source_id": source_id,
        "source_record_id": "native:" + digest(ident),
        "raw_record": copy.deepcopy(raw),
        "locator": {"file": path, **locator},
    }


def _header(rows, *, allow_trailing_empty_fields=False):
    """First maximal-width text row, supporting title/description prefixes.

    This is a structural heuristic, not proof of business semantics. Its exact
    choice and ambiguity are reported for the runtime proposal and review.
    """
    if len(rows) < 2:
        return None
    candidates = []
    for index, (_, row) in enumerate(rows[:25]):
        filled = [str(v).strip() for v in row if str(v).strip()]
        if not filled:
            continue
        if any(re.fullmatch(r"[\d\s.,+:/-]+", item) for item in filled):
            continue
        later = rows[index + 1 :]
        same_width = any(len(r) == len(row) for _, r in later)
        # Worksheet rows omit trailing empty cells. A header can therefore name
        # optional columns that are empty in every data row. Keep the CSV width
        # rule and require all nonempty worksheet rows to fit the header.
        later_widths = [len(r) for _, r in later if any(str(v).strip() for v in r)]
        trailing_empty = (
            allow_trailing_empty_fields
            and later_widths
            and max(later_widths) <= len(row)
        )
        if not later or not (same_width or trailing_empty):
            continue
        candidates.append((len(row), -index, index))
    if candidates:
        return max(candidates)[2]
    # Irregular headers still describe physical columns. Do not manufacture
    # names for duplicates, blanks or data extending beyond the labelled row.
    # This fallback is a reviewable structural proposal, not a semantic claim.
    for index, (_, row) in enumerate(rows[:25]):
        filled = [str(v).strip() for v in row if str(v).strip()]
        if len(filled) < 2 or any(
            re.fullmatch(r"[\d\s.,+:/-]+", item) for item in filled
        ):
            continue
        if any(any(str(v).strip() for v in r) for _, r in rows[index + 1 :]):
            candidates.append((len(filled), -index, index))
    return max(candidates)[2] if candidates else None


def _table(
    source_id,
    path,
    file_sha,
    rows,
    *,
    sheet=None,
    format_name="csv",
    details=None,
    sparse_physical_rows=False,
):
    index = _header(rows, allow_trailing_empty_fields=sparse_physical_rows)
    if index is None:
        return {
            "file": path,
            "format": format_name,
            "sheet": sheet,
            "status": "no_unambiguous_table",
            "records": [],
            "fields": [],
            "row_count": len(rows),
            "details": details or {},
        }
    physical, original_headers = rows[index]
    headers = [str(v).strip() for v in original_headers]
    width = max(len(headers), *(len(r) for _, r in rows[index + 1 :]))
    counts = Counter(headers)
    fields, columns = [], []
    irregular = width > len(headers) or not all(headers) or len(counts) != len(headers)
    for column in range(width):
        label = headers[column] if column < len(headers) else ""
        field = label
        if not label or counts[label] > 1:
            field = "native-column:" + digest(
                {"file": path, "sheet": sheet, "column": column + 1}
            )
            while field in headers:
                field = "@" + field
        fields.append(field)
        columns.append(
            {
                "source_field": field,
                "column": column + 1,
                "header": str(original_headers[column])
                if column < len(original_headers)
                else None,
                "binding": "exact_header" if field == label else "physical_column_only",
            }
        )
    records = []
    gap = False
    previous_physical = physical
    for row_number, values in rows[index + 1 :]:
        # Sparse worksheet XML can omit blank physical rows. CSV readers yield
        # every logical record, including blanks; their physical end-row jumps
        # instead belong to quoted multiline cells and must preserve one table.
        if sparse_physical_rows and records and row_number > previous_physical + 1:
            gap = True
        previous_physical = row_number
        if not any(str(v).strip() for v in values):
            gap = bool(records)
            continue
        if gap or [str(v).strip() for v in values] == headers:
            return {
                "file": path,
                "format": format_name,
                "sheet": sheet,
                "status": "unsupported_table",
                "records": [],
                "fields": [],
                "row_count": len(rows),
                "details": details or {},
                "reader_issue": {
                    "kind": "separated_or_repeated_table_regions",
                    "row": row_number,
                    "reason": "The table boundary is ambiguous; original bytes are retained and no rows from this table are emitted.",
                },
            }
        padded = list(values) + [""] * (width - len(values))
        raw = dict(
            zip(
                fields,
                ["" if v is None else str(v) for v in padded],
            )
        )
        locator = {"row": row_number}
        if sheet is not None:
            locator["sheet"] = sheet
        records.append(_record(source_id, path, file_sha, locator, raw))
    result = {
        "file": path,
        "format": format_name,
        "sheet": sheet,
        "header_row": physical,
        "status": "table",
        "fields": fields,
        "records": records,
        "row_count": len(records),
        "details": details or {},
        "header_method": "text_header_with_physical_columns; review_required"
        if irregular
        else "first_maximal_width_unique_text_row; review_required",
    }
    if irregular:
        result["columns"] = columns
    return result


def _csv_tables(source_id, path, data):
    text, encoding = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:16000], delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        # A title prefix can defeat Sniffer; consistent tab/comma width is a
        # structural fallback, with no dataset-specific labels or source names.
        delimiter = max(
            (",", "\t", ";", "|"),
            key=lambda d: sum(line.count(d) for line in text.splitlines()[:25]),
        )
    csv.field_size_limit(MAX_CELL_CHARS)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    rows = []
    try:
        for values in reader:
            if len(rows) >= MAX_ROWS or any(len(v) > MAX_CELL_CHARS for v in values):
                raise Failure("resource_limit", "CSV exceeds row/cell bound")
            rows.append((reader.line_num, values))
    except csv.Error as exc:
        raise Failure("parse_error", str(exc)) from exc
    return [
        _table(
            source_id,
            path,
            byte_hash(data),
            rows,
            format_name="tsv" if delimiter == "\t" else "csv",
            details={"encoding": encoding, "delimiter": delimiter},
        )
    ]


def _json_tables(source_id, path, data):
    text, encoding = decode_text(data)
    try:
        document = strict_json(text)
    except RecursionError as exc:
        raise Failure("resource_limit", "JSON exceeds parser nesting limit") from exc
    arrays = []

    def visit(item, pointer, depth=0):
        if depth > 20:
            raise Failure("resource_limit", "JSON nesting exceeds 20")
        if isinstance(item, list):
            if item and all(isinstance(row, dict) for row in item):
                arrays.append((pointer, item))
            # Record arrays are terminal: nested business arrays are retained raw.
        elif isinstance(item, dict):
            for key, value in item.items():
                visit(
                    value,
                    pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                    depth + 1,
                )

    visit(document, "")
    if not arrays:
        raise Failure("validation_empty", "No nonempty JSON array of objects")
    file_sha = byte_hash(data)
    tables = []
    for pointer, rows in arrays:
        if len(rows) > MAX_ROWS:
            raise Failure("resource_limit", "JSON exceeds row bound")
        records = [
            _record(source_id, path, file_sha, {"pointer": pointer + "/" + str(i)}, raw)
            for i, raw in enumerate(rows)
        ]
        tables.append(
            {
                "file": path,
                "format": "json",
                "record_pointer": pointer,
                "status": "table",
                "records": records,
                "row_count": len(records),
                "fields": sorted(set().union(*(leaf_fields(r) for r in rows))),
                "details": {"encoding": encoding},
            }
        )
    return tables


def relationship_target(source_part, target):
    """Resolve an internal OPC URI against its owning part, never the .rels path."""
    if (
        not isinstance(target, str)
        or not target
        or any(ord(char) < 32 for char in target)
    ):
        raise Failure("parse_error", "Invalid internal OOXML relationship")
    try:
        uri = urlsplit(target)
        path = unquote(uri.path, errors="strict")
    except (ValueError, UnicodeError) as exc:
        raise Failure("parse_error", "Invalid internal OOXML relationship URI") from exc
    if (
        uri.scheme
        or uri.netloc
        or uri.query
        or uri.fragment
        or "\\" in path
        or "\0" in path
    ):
        raise Failure("parse_error", "Unsupported internal OOXML relationship URI")
    parts = (
        [] if path.startswith("/") else list(PurePosixPath(source_part).parent.parts)
    )
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise Failure("parse_error", "OOXML relationship escapes package root")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise Failure("parse_error", "OOXML relationship does not name a part")
    return "/".join(parts)


def _worksheet_documentation(rows):
    """Recognize a sheet's explicit self-description, never its sheet name.

    All three signals are required so instructions quoted in a business row,
    an incidental title, or an example marker alone cannot discard a table.
    """
    title = any(
        len(values) == 1
        and re.fullmatch(r".*\binstructions\s*", str(values[0]).strip(), re.I)
        for _, values in rows[:5]
    )
    declaration = any(
        len(values) == 1
        and re.fullmatch(
            r"do not add data to this instructions? sheet(?:[\s,;:.].*)?",
            str(values[0]).strip(),
            re.I,
        )
        for _, values in rows
    )
    example = any(
        values and str(values[0]).strip().casefold() == "example only"
        for _, values in rows
    )
    return bool(title and declaration and example)


def _xlsx_tables(source_id, path, data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Failure("parse_error", "Invalid ZIP") from exc
    names = archive.namelist()
    if len(set(names)) != len(names) or len(names) > 2000:
        raise Failure("resource_limit", "Duplicate or excessive ZIP entries")
    if sum(i.file_size for i in archive.infolist()) > MAX_ZIP_BYTES:
        raise Failure("resource_limit", "Expanded XLSX exceeds byte bound")
    for name in names:
        if name.startswith("/") or ".." in PurePosixPath(name).parts:
            raise Failure("parse_error", "Unsafe OOXML part path")

    def xml(name):
        try:
            raw = archive.read(name)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise Failure("parse_error", "XML declarations are unsupported")
            node = ET.fromstring(raw)
            if sum(1 for _ in node.iter()) > MAX_CELLS * 4:
                raise Failure("resource_limit", "XML node bound exceeded")
            return node
        except (KeyError, ET.ParseError) as exc:
            raise Failure("parse_error", "Missing/invalid OOXML part: " + name) from exc

    book = xml("xl/workbook.xml")
    rels = xml("xl/_rels/workbook.xml.rels")
    targets = {}
    for relation in rels:
        if relation.get("TargetMode") == "External":
            continue
        targets[relation.get("Id")] = relationship_target(
            "xl/workbook.xml", relation.get("Target", "")
        )
    props = book.find("m:workbookPr", NS)
    date1904 = props is not None and props.get("date1904", "0").lower() in ("1", "true")
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = [
            "".join(n.text or "" for n in si.findall(".//m:t", NS))
            for si in xml("xl/sharedStrings.xml").findall("m:si", NS)
        ]
    formats, styles = {}, []
    if "xl/styles.xml" in names:
        style_root = xml("xl/styles.xml")
        formats = {
            int(n.get("numFmtId")): n.get("formatCode", "")
            for n in style_root.findall("m:numFmts/m:numFmt", NS)
        }
        styles = [
            int(n.get("numFmtId", "0"))
            for n in style_root.findall("m:cellXfs/m:xf", NS)
        ]
    tables, total_cells = [], 0
    for sheet in book.findall("m:sheets/m:sheet", NS):
        target = targets.get(sheet.get("{" + NS["r"] + "}id"))
        if not target:
            raise Failure("parse_error", "Sheet relationship missing")
        root = xml(target)
        merged = [
            cell.get("ref", "") for cell in root.findall("m:mergeCells/m:mergeCell", NS)
        ]
        rows, warnings = [], []
        for row in root.findall("m:sheetData/m:row", NS):
            if len(rows) >= MAX_ROWS:
                raise Failure("resource_limit", "XLSX row bound exceeded")
            cells = {}
            for cell in row.findall("m:c", NS):
                total_cells += 1
                if total_cells > MAX_CELLS:
                    raise Failure("resource_limit", "XLSX cell bound exceeded")
                match = re.fullmatch(r"([A-Z]+)([0-9]+)", cell.get("r", ""))
                if not match:
                    raise Failure("parse_error", "Invalid cell coordinate")
                column = 0
                for char in match[1]:
                    column = column * 26 + ord(char) - 64
                if column > 16384:
                    raise Failure("resource_limit", "XLSX column exceeds format bound")
                value_node = cell.find("m:v", NS)
                value = (
                    value_node.text
                    if value_node is not None and value_node.text is not None
                    else ""
                )
                if cell.find("m:f", NS) is not None:
                    if value_node is None:
                        raise Failure(
                            "unsupported_format",
                            "Formula has no cached value",
                            cell=cell.get("r"),
                        )
                    warnings.append(
                        {
                            "cell": cell.get("r"),
                            "limitation": "cached_formula_value_not_recalculated",
                        }
                    )
                kind = cell.get("t", "n")
                if kind == "s":
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError) as exc:
                        raise Failure(
                            "parse_error", "Invalid shared-string index"
                        ) from exc
                elif kind == "inlineStr":
                    value = "".join(n.text or "" for n in cell.findall(".//m:t", NS))
                elif kind == "b":
                    value = "TRUE" if value == "1" else "FALSE"
                elif kind == "e":
                    raise Failure(
                        "unsupported_format", "Excel error cell", cell=cell.get("r")
                    )
                elif value and kind == "n":
                    style = int(cell.get("s", "0"))
                    fmt_id = styles[style] if style < len(styles) else 0
                    fmt = formats.get(fmt_id, "")
                    if fmt_id in range(14, 23) or (
                        fmt
                        and re.search(
                            r"[yd]", re.sub(r'"[^"]*"|\[[^\]]*\]', "", fmt), re.I
                        )
                    ):
                        try:
                            serial = float(value)
                            if not date1904 and 60 <= serial < 61:
                                raise Failure(
                                    "unsupported_format", "Fictitious Excel 1900-02-29"
                                )
                            epoch = (
                                datetime(1904, 1, 1)
                                if date1904
                                else datetime(1899, 12, 31)
                            )
                            adjusted = serial if date1904 or serial < 60 else serial - 1
                            converted = epoch + timedelta(days=adjusted)
                            value = (
                                converted.date().isoformat()
                                if serial.is_integer()
                                else converted.isoformat()
                            )
                        except (ValueError, OverflowError) as exc:
                            raise Failure("parse_error", "Invalid Excel date") from exc
                    elif re.fullmatch(r"0+", fmt) and re.fullmatch(r"\d+", value):
                        value = value.zfill(len(fmt))
                    elif re.search(r"[eE]", value) or (
                        len(re.sub(r"\D", "", value)) > 15
                    ):
                        warnings.append(
                            {
                                "cell": cell.get("r"),
                                "limitation": "numeric_precision_or_scientific_notation;_identifier_unsafe",
                            }
                        )
                if len(value) > MAX_CELL_CHARS:
                    raise Failure("resource_limit", "XLSX cell text exceeds bound")
                cells[column] = value
            values = [cells.get(i, "") for i in range(1, max(cells, default=0) + 1)]
            while values and values[-1] == "":
                values.pop()
            rows.append((int(row.get("r")), values))
        if _worksheet_documentation(rows):
            tables.append(
                {
                    "file": path,
                    "format": "xlsx",
                    "sheet": sheet.get("name"),
                    "status": "documentation_sheet",
                    "records": [],
                    "fields": [],
                    "row_count": len(rows),
                    "details": {
                        "date_system": "1904" if date1904 else "1900",
                        "warnings": warnings,
                        "classification_basis": "instruction_title;explicit_no_data_declaration;example_only_marker",
                    },
                }
            )
            continue
        table = _table(
            source_id,
            path,
            byte_hash(data),
            rows,
            sheet=sheet.get("name"),
            format_name="xlsx",
            details={
                "date_system": "1904" if date1904 else "1900",
                "warnings": warnings,
            },
            sparse_physical_rows=True,
        )
        if merged:
            for reference in merged:
                match = re.fullmatch(
                    r"[A-Z]+([1-9][0-9]*):[A-Z]+([1-9][0-9]*)", reference
                )
                if not match or int(match[1]) > int(match[2]):
                    raise Failure(
                        "parse_error", "Invalid merged-cell range", range=reference
                    )
            table["details"]["merged_ranges"] = merged
            table["merged_ranges"] = merged
            table["merge_policy"] = (
                "physical_anchor_only; no_fill; header_and_subject_roles_require_review"
            )
        tables.append(table)
    return tables


def read_native(root, relative, source_id):
    path = safe_path(root, relative)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise Failure("resource_limit", "Native file exceeds byte bound")
    data = path.read_bytes()
    if data.startswith(b"PK\x03\x04"):
        return _xlsx_tables(source_id, relative, data)
    text, _ = decode_text(data)
    if text.lstrip().startswith(("{", "[")):
        return _json_tables(source_id, relative, data)
    return _csv_tables(source_id, relative, data)


def public_structure(tables):
    """No validation raw values or value-derived summaries enter this view."""
    return [
        {
            key: copy.deepcopy(value)
            for key, value in table.items()
            if key
            in {
                "file",
                "format",
                "sheet",
                "record_pointer",
                "header_row",
                "status",
                "fields",
                "header_method",
                "columns",
                "reader_issue",
                "merged_ranges",
                "merge_policy",
            }
        }
        for table in tables
    ]


def partition_records(records, exposed_ids=()):
    groups = defaultdict(list)
    for record in records:
        groups[digest(record["raw_record"])].append(record)
    if len(groups) < 2:
        raise Failure(
            "validation_empty",
            "At least two distinct business-content groups are required",
        )
    exposed = set(exposed_ids)
    forced = {
        key
        for key, group in groups.items()
        if any(r["source_record_id"] in exposed for r in group)
    }
    available = sorted(set(groups) - forced)
    if not available:
        raise Failure(
            "validation_empty", "All business content was exposed to proposal"
        )
    desired = max(1, min(8, len(groups) // 3))
    proposal_groups = forced | set(available[: max(0, desired - len(forced))])
    if not proposal_groups:
        proposal_groups.add(available[0])
    if len(proposal_groups) == len(groups):
        raise Failure("validation_empty", "No independent validation group remains")

    def artifact(selected):
        result = [
            {
                key: copy.deepcopy(record[key])
                for key in ("source_id", "source_record_id", "raw_record")
            }
            for group in selected
            for record in groups[group]
        ]
        return sorted(result, key=lambda r: (r["source_id"], r["source_record_id"]))

    return artifact(proposal_groups), artifact(set(groups) - proposal_groups)
