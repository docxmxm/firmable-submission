"""Read-only original document pages; paths come only from the evidence manifest."""

import io
import re
from pathlib import Path

from pypdf import PdfReader

from .common import Failure, byte_hash, canonical, digest, safe_path
from .native import MAX_FILE_BYTES, decode_text

MAX_CONTEXT_BYTES = 24 * 1024
MAX_CONTEXT_PAGES = 3
MAX_DOCUMENT_PAGES = 500


def source_url_candidates(catalogue_metadata, *, pages=()):
    """Locate literal navigation URLs, without judging authority or field meaning.

    Offsets are characters in the metadata string or extracted original page.
    Keep each occurrence's provenance, including repeated URLs in other places.
    Fetch policy and semantic support remain the caller's separate concerns.
    """
    candidates = []

    def scan(text, source_ref):
        for match in re.finditer(r"https?://[^\s<>\"']+", text, re.IGNORECASE):
            url = match.group()
            # Remove prose/Markdown delimiters, preserving balanced URL brackets.
            if match.start() or match.end() != len(text):
                url = url.rstrip(".,;:!?")
            for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
                while url.endswith(closing) and url.count(closing) > url.count(opening):
                    url = url[:-1]
            candidates.append(
                {
                    "url": url,
                    "source_ref": {
                        **source_ref,
                        "start": match.start(),
                        "end": match.start() + len(url),
                    },
                }
            )

    metadata_hash = digest(catalogue_metadata)

    def visit(value, pointer):
        if isinstance(value, dict):
            for key in sorted(value):
                visit(
                    value[key],
                    pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, pointer + "/" + str(index))
        elif isinstance(value, str):
            scan(
                value,
                {
                    "kind": "catalogue_metadata",
                    "metadata_sha256": metadata_hash,
                    "json_pointer": pointer,
                },
            )

    visit(catalogue_metadata, "")
    for page in pages:
        scan(
            page["text"],
            {
                "kind": "original_document",
                **{
                    key: page[key]
                    for key in (
                        "file",
                        "file_sha256",
                        "page_number",
                        "text_sha256",
                        "extraction",
                    )
                },
            },
        )
    return candidates


def document_pages(root, manifest):
    """Extract page text from manifested PDF/TXT/MD, never native data files.

    PDF page numbers are one-based physical pages. Text documents use formfeed
    page breaks. No OCR, network access, embedded actions or arbitrary paths are
    used. An empty PDF page is marked explicitly, not treated as absent evidence.
    """
    if not isinstance(manifest, list):
        raise Failure("validation_error", "Document tools require the source manifest")
    documents, pages = [], []
    for entry in manifest:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise Failure("validation_error", "Malformed document manifest entry")
        relative = entry["path"]
        suffix = Path(relative).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            continue
        path = safe_path(root, relative)
        data = path.read_bytes()
        file_hash = byte_hash(data)
        if file_hash != entry.get("sha256"):
            raise Failure(
                "identity_mismatch", "Original document changed after manifest"
            )
        if len(data) > MAX_FILE_BYTES:
            raise Failure("resource_limit", "Original document exceeds file size limit")
        extraction = "pypdf_layout" if suffix == ".pdf" else "original_text_formfeed"
        metadata = {
            "file": relative,
            "file_sha256": file_hash,
            "format": suffix[1:],
            "extraction": extraction,
            "page_count": 0,
            "status": "extracted",
            "ocr_required_pages": [],
        }
        try:
            if suffix == ".pdf":
                reader = PdfReader(io.BytesIO(data))
                if reader.is_encrypted:
                    metadata["status"] = "encrypted_document_unsupported"
                    documents.append(metadata)
                    continue
                if len(reader.pages) > MAX_DOCUMENT_PAGES:
                    raise Failure("resource_limit", "Original PDF exceeds page limit")
                texts = [
                    (page.extract_text(extraction_mode="layout") or "")
                    if page.get_contents() is not None
                    else ""
                    for page in reader.pages
                ]
            else:
                text, encoding = decode_text(data)
                metadata["encoding"] = encoding
                texts = text.split("\f")
                if text.endswith("\f"):
                    texts.pop()
                if len(texts) > MAX_DOCUMENT_PAGES:
                    raise Failure("resource_limit", "Original text exceeds page limit")
        except Failure:
            raise
        except Exception as exc:
            metadata.update(
                status="document_extraction_failed", error_type=type(exc).__name__
            )
            documents.append(metadata)
            continue
        metadata["page_count"] = len(texts)
        for number, text in enumerate(texts, 1):
            encoded = text.encode("utf-8")
            needs_ocr = suffix == ".pdf" and not text.strip()
            if needs_ocr:
                metadata["ocr_required_pages"].append(number)
            pages.append(
                {
                    "file": relative,
                    "file_sha256": file_hash,
                    "page_number": number,
                    "text": text,
                    "text_sha256": byte_hash(encoded),
                    "utf8_bytes": len(encoded),
                    "extraction": extraction,
                    "ocr_required": needs_ocr,
                    "status": "no_extractable_text_ocr_required"
                    if needs_ocr
                    else "extracted",
                }
            )
        if metadata["ocr_required_pages"]:
            metadata["status"] = "some_pages_have_no_extractable_text"
        documents.append(metadata)
    return {"documents": documents, "pages": pages}


def inspect_document_context(root, manifest, query, max_pages=MAX_CONTEXT_PAGES):
    """Return complete literal-hit pages and neighbors within one bounded result.

    Rank by literal occurrence count, preferring PDF on ties. For each ranked hit
    include the hit, following page, then preceding page. Omit oversized pages
    completely. Original text is never truncated to fit the context budget.
    """
    if not isinstance(query, str) or not query or len(query) > 256 or "\x00" in query:
        raise Failure("validation_error", "Document query must be bounded literal text")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_CONTEXT_PAGES:
        raise Failure("validation_error", "Document context allows one to three pages")
    extracted = document_pages(root, manifest)
    indexed = {(p["file"], p["page_number"]): p for p in extracted["pages"]}
    needle = query.casefold()
    matches = []
    for page in extracted["pages"]:
        count = page["text"].casefold().count(needle)
        if count:
            matches.append((page, count))
    matches.sort(
        key=lambda pair: (
            -pair[1],
            pair[0]["extraction"] != "pypdf_layout",
            pair[0]["file"],
            pair[0]["page_number"],
        )
    )
    matching_keys = {
        (page["file"], page["page_number"]): count for page, count in matches
    }
    candidates, seen = [], set()
    for page, _ in matches:
        for number in (
            page["page_number"],
            page["page_number"] + 1,
            page["page_number"] - 1,
        ):
            key = page["file"], number
            if key in indexed and key not in seen:
                candidates.append(indexed[key])
                seen.add(key)
    result = {
        "version": "original-document-context-1",
        "query": query,
        "scope": "manifested_original_documents_only_no_native_rows",
        "match_mode": "case_insensitive_literal_substring",
        "selection": "Literal occurrence count descending, PDF before text on ties; hit then following then preceding page. Complete pages only.",
        "matching_pages": [
            {
                "file": p["file"],
                "page_number": p["page_number"],
                "literal_match_count": count,
            }
            for p, count in matches
        ],
        "pages": [],
        "documents": extracted["documents"],
        "truncated": False,
        "omitted_page_count": 0,
        "omitted_pages": [],
        "omitted_matching_page_ref_count": 0,
        "omitted_document_metadata_count": 0,
        "limits": {"max_pages": max_pages, "total_json_utf8_bytes": MAX_CONTEXT_BYTES},
        "limitations": [
            "A literal hit or page layout does not establish semantic support or field ownership.",
            "No OCR was performed. A PDF page with no extractable text may be scanned or blank; OCR is required to assess its contents.",
            "PDF extracted layout text is tied to original file/page hashes, not pixel-perfect rendering or original PDF byte offsets.",
        ],
    }
    # Extremely large manifests can exceed the result bound before page text.
    # Keep every hit reference when it fits; any metadata omission is explicit.
    while len(canonical(result)) > MAX_CONTEXT_BYTES and result["matching_pages"]:
        result["matching_pages"].pop()
        result["omitted_matching_page_ref_count"] += 1
    while len(canonical(result)) > MAX_CONTEXT_BYTES and result["documents"]:
        result["documents"].pop()
        result["omitted_document_metadata_count"] += 1
    for page in candidates:
        key = page["file"], page["page_number"]
        item = {
            **page,
            "literal_match_count": matching_keys.get(key, 0),
            "context_role": "literal_hit" if key in matching_keys else "adjacent_page",
        }
        reason = None
        if page["utf8_bytes"] > MAX_CONTEXT_BYTES:
            reason = "oversize_complete_page"
        elif len(result["pages"]) >= max_pages:
            reason = "page_limit"
        else:
            result["pages"].append(item)
            if len(canonical(result)) > MAX_CONTEXT_BYTES:
                result["pages"].pop()
                reason = "total_json_byte_limit"
        if reason:
            result["omitted_page_count"] += 1
            omitted = {
                "file": page["file"],
                "page_number": page["page_number"],
                "reason": reason,
            }
            result["omitted_pages"].append(omitted)
            # Metadata itself is bounded. The count still reports all omissions.
            if len(canonical(result)) > MAX_CONTEXT_BYTES:
                result["omitted_pages"].pop()
    result["truncated"] = bool(
        result["omitted_page_count"]
        or result["omitted_matching_page_ref_count"]
        or result["omitted_document_metadata_count"]
    )
    # Reserve space for final counters and truncated flag without cutting text.
    while len(canonical(result)) > MAX_CONTEXT_BYTES:
        result["truncated"] = True
        if result["omitted_pages"]:
            result["omitted_pages"].pop()
        elif result["matching_pages"]:
            result["matching_pages"].pop()
            result["omitted_matching_page_ref_count"] += 1
        elif result["documents"]:
            result["documents"].pop()
            result["omitted_document_metadata_count"] += 1
        else:
            result["pages"].pop()
            result["omitted_page_count"] += 1
    return result
