#!/usr/bin/env python3
"""Discover public CKAN metadata and reproducibly rank business datasets.

Uses no model calls. The score is a frozen metadata heuristic, not a probability
that records contain businesses. Resource reachability and row semantics are
separate checks. ``audit20.csv`` deliberately contains no purported human labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PORTALS = {
    "national": "https://data.gov.au/data/api/3/action/",
    "qld": "https://www.data.qld.gov.au/api/3/action/",
}
DATASET_BASES = {
    "national": "https://data.gov.au/data/dataset/",
    "qld": "https://www.data.qld.gov.au/dataset/",
}
# Broad discovery retains unrelated and aggregate records; the focused queries
# improve coverage of business-level registers without hard-coding any dataset.
QUERIES = (
    "business",
    "company",
    "register",
    "licence",
    "contracts",
    "tax",
    '"business names"',
    '"licensed contractors"',
    '"corporate tax"',
    "AusTender",
)
SCORING_VERSION = "metadata-business-v2"
SCORING_RULES = (
    (
        "identifier",
        5,
        "metadata",
        r"\b(?:ABN|ACN|NZBN)\b|Australian (?:Business|Company) Number",
    ),
    (
        "entity_title",
        4,
        "title",
        r"\b(?:compan(?:y|ies)|corporate|business names?|contractors?|suppliers?|licensees?|charit(?:y|ies))\b",
    ),
    (
        "record_title",
        3,
        "title",
        r"\b(?:register|licen[cs]es?|contracts?|permits?|directory|directories)\b|AusTender",
    ),
    (
        "procurement_title",
        3,
        "title",
        r"\b(?:contracts?|procurement|tenders?)\b|AusTender",
    ),
    (
        "entity_grain",
        2,
        "metadata",
        r"\b(?:suppliers?|contractors?|licensees?)\b|(?:company|business|legal|entity) names?",
    ),
    (
        "aggregate_title",
        -4,
        "title",
        r"\b(?:statistics|statistical|counts?|totals?|aggregat(?:e|ed|ion)|summar(?:y|ies)|time series)\b",
    ),
)
MACHINE_FORMATS = frozenset(
    {"CSV", "TSV", "XLS", "XLSX", "JSON", "XML", "JSONL", "NDJSON"}
)
UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
OUTPUT_FIELDS = [
    "rank",
    "dataset_id",
    "title",
    "publisher",
    "formats",
    "confidence",
    "confidence_kind",
    "reason",
    "score",
    "portal",
    "source_url",
    "catalog_key",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(metadata: dict, portal: str) -> str:
    """Use shared UUIDs across portals; preserve distinct non-UUID namespaces."""
    original = {
        item.get("key"): item.get("value")
        for item in metadata.get("extras", [])
        if isinstance(item, dict)
    }
    # Harvested copies may expose their owning source UUID explicitly.
    for key in ("original_id", "source_dataset_id"):
        candidate = str(original.get(key) or "")
        if UUID.fullmatch(candidate):
            return candidate.lower()
    dataset_id = str(metadata.get("id") or metadata.get("name") or "")
    if not dataset_id:
        raise ValueError("Catalogue result lacks dataset id and name")
    return (
        dataset_id.lower() if UUID.fullmatch(dataset_id) else f"{portal}:{dataset_id}"
    )


def normalize(metadata: dict, portal: str) -> dict:
    dataset_id = str(metadata.get("id") or metadata.get("name") or "")
    organization = metadata.get("organization") or {}
    publisher = (
        organization.get("title")
        or organization.get("name")
        or metadata.get("author")
        or metadata.get("maintainer")
        or "unknown"
    )
    formats = sorted(
        {
            str(resource.get("format") or "").strip().upper()
            for resource in metadata.get("resources", [])
        }
        - {""}
    )
    return {
        "catalog_key": identity(metadata, portal),
        "dataset_id": dataset_id,
        "title": metadata.get("title") or metadata.get("name") or dataset_id,
        "publisher": publisher,
        "formats": formats,
        "portal": portal,
        "source_url": DATASET_BASES[portal] + dataset_id,
        "portals": [portal],
        "aliases": [{"portal": portal, "dataset_id": dataset_id}],
        "metadata": metadata,
    }


def deduplicate(results: list[tuple[str, dict]]) -> list[dict]:
    """First response wins metadata; merge provenance and resource formats."""
    unique: dict[str, dict] = {}
    for portal, metadata in results:
        record = normalize(metadata, portal)
        existing = unique.get(record["catalog_key"])
        if existing is None:
            unique[record["catalog_key"]] = record
            continue
        existing["formats"] = sorted(set(existing["formats"]) | set(record["formats"]))
        if portal not in existing["portals"]:
            existing["portals"].append(portal)
        for alias in record["aliases"]:
            if alias not in existing["aliases"]:
                existing["aliases"].append(alias)
    return sorted(unique.values(), key=lambda record: record["catalog_key"])


def score_record(record: dict) -> dict:
    metadata = record["metadata"]
    title = str(record["title"])
    tags = " ".join(
        str(tag.get("display_name") or tag.get("name") or "")
        for tag in metadata.get("tags", [])
    )
    resource_text = " ".join(
        str(resource.get("name") or "") + " " + str(resource.get("description") or "")
        for resource in metadata.get("resources", [])
    )
    text = " ".join((title, str(metadata.get("notes") or ""), tags, resource_text))
    features = []
    for label, weight, scope, pattern in SCORING_RULES:
        if re.search(pattern, title if scope == "title" else text, flags=re.I):
            features.append({"feature": label, "weight": weight})
    if set(record["formats"]) & MACHINE_FORMATS:
        features.append({"feature": "machine_resource", "weight": 1})
    else:
        features.append({"feature": "no_machine_resource", "weight": -6})
    score = sum(feature["weight"] for feature in features)
    # Transparent monotonic transform only. This is neither trained nor calibrated.
    confidence = round(max(0.0, min(1.0, (score + 10) / 28)), 4)
    result = {
        field: record[field]
        for field in (
            "dataset_id",
            "title",
            "publisher",
            "formats",
            "portal",
            "source_url",
            "catalog_key",
        )
    }
    result.update(
        {
            "score": score,
            "confidence": confidence,
            "confidence_kind": "uncalibrated_metadata_heuristic",
            "reason": "; ".join(
                f"{feature['feature']} ({feature['weight']:+d})" for feature in features
            )
            or "no business-record features",
            "features": features,
            "scoring_version": SCORING_VERSION,
        }
    )
    return result


def rank_records(records: list[dict], limit: int = 50) -> list[dict]:
    ranked = sorted(
        (score_record(record) for record in records),
        key=lambda record: (
            -record["score"],
            record["dataset_id"],
            record["catalog_key"],
        ),
    )[:limit]
    return [{"rank": index, **record} for index, record in enumerate(ranked, start=1)]


def write_csv(path: Path, records: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    field: json.dumps(record[field], ensure_ascii=False)
                    if isinstance(record.get(field), (list, dict))
                    else record.get(field, "")
                    for field in fields
                }
            )


def scoring_manifest() -> dict:
    return {
        "version": SCORING_VERSION,
        "rules": [
            {"feature": label, "weight": weight, "scope": scope, "pattern": pattern}
            for label, weight, scope, pattern in SCORING_RULES
        ],
        "machine_formats": sorted(MACHINE_FORMATS),
        "machine_weight": 1,
        "no_machine_weight": -6,
        "confidence_formula": "clamp((score + 10) / 28, 0, 1), round to 4 decimal places",
        "confidence_kind": "uncalibrated_metadata_heuristic",
        "sort": ["score descending", "dataset_id ascending", "catalog_key ascending"],
        "deduplication": "source UUID from original_id/source_dataset_id extras, else dataset UUID, else portal:id; first response metadata wins",
        "development_revision": "v1 metadata ranking exposed PDF geological reports with company/permit titles; v2 applies a generic non-machine resource penalty and a generic procurement title feature. v1 ranking and untouched human-audit template retained separately; no audit labels or dataset-ID bonuses were used.",
        "warning": "Metadata plausibility only; no claim of record availability, commercial entity grain or calibrated probability.",
    }


def rank_snapshot(output: Path, minimum: int, seed: int) -> dict:
    catalog_path = output / "catalog.jsonl"
    records = read_jsonl(catalog_path)
    if len(records) < minimum:
        raise ValueError(
            f"Only {len(records)} unique catalog records; required {minimum}"
        )
    ranked = rank_records(records)
    # The sample is selected from the frozen ranking, not from confirmed positives.
    sampled = random.Random(seed).sample(ranked, min(20, len(ranked)))
    audits = [
        {
            **record,
            "review_status": "pending_human_review",
            "contains_identifiable_business_entities": "",
            "is_false_positive": "",
            "evidence_url": "",
            "review_note": "",
            "reviewer": "",
            "reviewed_at": "",
        }
        for record in sampled
    ]
    reviewed_audit = False
    audit_path = output / "audit20.csv"
    if audit_path.exists():
        with audit_path.open(encoding="utf-8", newline="") as stream:
            existing_audit = list(csv.DictReader(stream))
        reviewed_audit = any(
            record.get(field)
            for record in existing_audit
            for field in (
                "contains_identifiable_business_entities",
                "is_false_positive",
                "review_note",
                "reviewer",
                "reviewed_at",
            )
        )
        if reviewed_audit and [record["dataset_id"] for record in existing_audit] != [
            record["dataset_id"] for record in audits
        ]:
            raise ValueError(
                "Existing human audit belongs to another ranking/seed; preserve it and use a new snapshot directory"
            )
    dump_json(output / "ranking-policy.json", scoring_manifest())
    write_jsonl(output / "shortlist50.jsonl", ranked)
    write_csv(output / "shortlist50.csv", ranked, OUTPUT_FIELDS)
    if not reviewed_audit:
        write_jsonl(output / "audit20.jsonl", audits)
        write_csv(
            audit_path,
            audits,
            OUTPUT_FIELDS
            + [
                "review_status",
                "contains_identifiable_business_entities",
                "is_false_positive",
                "evidence_url",
                "review_note",
                "reviewer",
                "reviewed_at",
            ],
        )
    manifest = {
        "ranked_at": utc_now(),
        "catalog_records": len(records),
        "shortlist_records": len(ranked),
        "audit_records": len(audits),
        "audit_seed": seed,
        "audit_status": "human_review_present_not_scored"
        if reviewed_audit
        else "pending_human_review",
        "false_positive_rate": None,
        "catalog_sha256": sha256_file(catalog_path),
        "shortlist_sha256": sha256_file(output / "shortlist50.jsonl"),
        "policy_sha256": sha256_file(output / "ranking-policy.json"),
        "script_sha256": sha256_file(Path(__file__)),
        "scoring": scoring_manifest(),
    }
    dump_json(output / "ranking-manifest.json", manifest)
    return manifest


def discover(
    output: Path, portals: list[str], rows: int, timeout: float, minimum: int, seed: int
) -> dict:
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if (output / "discovery-manifest.json").exists() or any(raw_dir.iterdir()):
        raise ValueError(
            f"Refusing to overwrite existing live snapshot: {output}; use another directory or replay"
        )
    started = utc_now()
    # Freeze the exact heuristic before fetching any of this run's metadata.
    dump_json(output / "scoring-rules.json", scoring_manifest())
    requests = []
    results: list[tuple[str, dict]] = []
    for portal in portals:
        for query_index, query in enumerate(QUERIES):
            parameters = {
                "q": query,
                "rows": rows,
                "start": 0,
                "sort": "score desc, id asc",
            }
            url = PORTALS[portal] + "package_search?" + urlencode(parameters)
            request_started = time.monotonic()
            entry = {
                "portal": portal,
                "action": "package_search",
                "parameters": parameters,
                "url": url,
                "requested_at": utc_now(),
            }
            raw_path = raw_dir / f"{portal}-{query_index:02d}.json"
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": "Firmable-assessment-catalog/1.0",
                        "Accept": "application/json",
                    },
                )
                with urlopen(request, timeout=timeout) as response:
                    body = response.read()
                    entry["http_status"] = response.status
                raw_path.write_bytes(body)
                entry["response_sha256"] = hashlib.sha256(body).hexdigest()
                entry["raw_path"] = str(raw_path.relative_to(output))
                payload = json.loads(body)
                if not payload.get("success"):
                    raise ValueError(f"CKAN success=false: {payload.get('error')}")
                result = payload["result"]
                metadata_records = result["results"]
                entry.update(
                    {
                        "status": "success",
                        "catalog_count": result.get("count"),
                        "returned_records": len(metadata_records),
                    }
                )
                results.extend((portal, metadata) for metadata in metadata_records)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as error:
                entry.update(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
                if isinstance(error, HTTPError):
                    entry["http_status"] = error.code
                    error_path = raw_dir / f"{portal}-{query_index:02d}-error.txt"
                    error_path.write_bytes(error.read())
                    entry["error_raw_path"] = str(error_path.relative_to(output))
            entry["elapsed_seconds"] = round(time.monotonic() - request_started, 3)
            requests.append(entry)
            write_jsonl(output / "requests.jsonl", requests)
            print(
                f"{portal}: {query!r}: {entry['status']} ({entry.get('returned_records', 0)} records)",
                file=sys.stderr,
                flush=True,
            )
    records = deduplicate(results)
    write_jsonl(output / "catalog.jsonl", records)
    manifest = {
        "started_at": started,
        "completed_at": utc_now(),
        "portals": portals,
        "queries": list(QUERIES),
        "rows_per_query": rows,
        "pagination": "one bounded page per distinct query; query count and requested rows retained; not a complete portal census",
        "retrieved_records_before_dedup": len(results),
        "unique_catalog_records": len(records),
        "request_count": len(requests),
        "failed_requests": sum(entry["status"] == "failed" for entry in requests),
        "minimum_required": minimum,
        "minimum_met": len(records) >= minimum,
        "catalog_sha256": sha256_file(output / "catalog.jsonl"),
        "script_sha256": sha256_file(Path(__file__)),
        "llm_calls": 0,
        "llm_api_cost_usd": 0,
    }
    dump_json(output / "discovery-manifest.json", manifest)
    ranking = rank_snapshot(output, minimum, seed)
    return {"discovery": manifest, "ranking": ranking}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("discover", "replay"))
    parser.add_argument("--output", type=Path, default=Path("data/catalog"))
    parser.add_argument(
        "--portals", nargs="+", choices=tuple(PORTALS), default=list(PORTALS)
    )
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--minimum", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args(argv)
    if not 1 <= args.rows <= 1000 or args.minimum < 200:
        parser.error("rows must be 1..1000 and minimum must be at least 200")
    try:
        result = (
            discover(
                args.output,
                args.portals,
                args.rows,
                args.timeout,
                args.minimum,
                args.seed,
            )
            if args.mode == "discover"
            else rank_snapshot(args.output, args.minimum, args.seed)
        )
    except (ValueError, OSError) as error:
        print(f"catalog failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
