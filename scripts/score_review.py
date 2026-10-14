"""Validate the bound 50-pair human review CSV and report conservative precision.

This script validates supplied judgments; it does not produce them or verify
that a person made them. Uncertain judgments are never counted as correct.
"""

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DECISIONS = {"correct", "incorrect", "uncertain"}
COLUMNS = ["pair_id", "decision", "reviewer", "notes"]


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def pair_id(pair):
    endpoints = sorted([pair["left"], pair["right"]])
    return sha256(json.dumps(endpoints, separators=(",", ":")).encode())


def score_review(review_dir):
    review_dir = Path(review_dir)
    summary = json.loads((review_dir / "summary.json").read_text())
    links_bytes = (review_dir.parent / "links.json").read_bytes()
    if sha256(links_bytes) != summary["links_sha256"]:
        raise ValueError("Links changed since this review sample was frozen.")
    links = json.loads(links_bytes)
    population = {}
    for entity in links["links"]:
        for pair in entity["pairs"]:
            key = pair_id(pair)
            if key in population:
                raise ValueError("Proposed link population contains duplicate pairs.")
            population[key] = pair

    samples_bytes = (review_dir / "samples.jsonl").read_bytes()
    samples_hash = sha256(samples_bytes)
    if summary.get("samples_sha256", samples_hash) != samples_hash:
        raise ValueError("Review evidence changed since the sample was frozen.")
    samples = [json.loads(line) for line in samples_bytes.splitlines() if line.strip()]
    expected = [sample["pair_id"] for sample in samples]
    if len(expected) != 50 or len(set(expected)) != 50:
        raise ValueError("Review evidence must contain exactly 50 distinct pairs.")
    if summary["sample_size"] != 50:
        raise ValueError("The bound sample size must be 50.")
    seed = summary["seed"]
    selected = sorted(
        population,
        key=lambda key: sha256((seed + ":" + key).encode()),
    )[:50]
    if set(expected) != set(selected):
        raise ValueError("Pair IDs differ from the frozen sampling policy.")
    observations = {}
    for path in sorted((review_dir.parent / "observations").glob("*.jsonl")):
        for line in path.read_bytes().splitlines():
            if not line.strip():
                continue
            observation = json.loads(line)
            key = (observation["source_id"], observation["source_record_id"])
            if key in observations:
                raise ValueError("Current observations contain duplicate identities.")
            observations[key] = observation
    for sample in samples:
        key = sample["pair_id"]
        if pair_id(sample) != key or key not in population:
            raise ValueError("Review evidence contains a pair outside this cohort.")
        for side in ("left", "right"):
            observation = sample[side + "_observation"]
            endpoint = [observation["source_id"], observation["source_record_id"]]
            if endpoint != sample[side] or sample[side] != population[key][side]:
                raise ValueError("Review evidence has a mismatched record endpoint.")
            if observation != observations.get(tuple(endpoint)):
                raise ValueError("Review evidence differs from the current observation.")

    reviews_bytes = (review_dir / "reviews.csv").read_bytes()
    reader = csv.DictReader(io.StringIO(reviews_bytes.decode("utf-8-sig")))
    if reader.fieldnames != COLUMNS:
        raise ValueError("CSV columns must be: " + ",".join(COLUMNS))
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Malformed CSV row: missing or extra cells.")
    actual = [row["pair_id"].strip() for row in rows]
    if len(actual) != 50 or len(set(actual)) != 50 or set(actual) != set(expected):
        raise ValueError("CSV must contain every one of the 50 frozen pair IDs exactly once.")
    counts = Counter()
    reviewers = set()
    for row in rows:
        decision, reviewer = row["decision"].strip(), row["reviewer"].strip()
        if not decision:
            counts["unreviewed"] += 1
            continue
        if decision not in DECISIONS:
            raise ValueError("Decision must be correct, incorrect, uncertain, or blank.")
        if not reviewer:
            raise ValueError("Every supplied judgment must name its reviewer.")
        counts[decision] += 1
        reviewers.add(reviewer)
    complete = counts["unreviewed"] == 0
    result = {
        **summary,
        "samples_sha256": samples_hash,
        "reviews_sha256": sha256(reviews_bytes),
        "human_reviewed": 50 - counts["unreviewed"],
        "review_status": "complete" if complete else "pending_human_review",
        "decision_counts": {
            name: counts[name] for name in ("correct", "incorrect", "uncertain", "unreviewed")
        },
        "reviewers": sorted(reviewers),
        "precision": counts["correct"] / 50 if complete else None,
        "precision_basis": (
            "Conservative confirmed-correct fraction of all 50 supplied reviews; "
            "uncertain is nonconfirmed, never correct. Precision is null until all "
            "50 decisions and reviewer names are supplied."
        ),
        "judgment_origin": (
            "Reviewer-supplied CSV judgments. This script generates no judgments "
            "and cannot attest that the entries were made by a human."
        ),
        "limitation": (
            "A 50-pair sample at the recorded threshold, not model ground truth "
            "or a measurement of unlinked-record recall."
            if complete
            else "Pending human decisions; precision remains unknown."
        ),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-dir", type=Path, default=PROJECT / "outputs/human-review"
    )
    parser.add_argument("--check-only", action="store_true", help="Validate without writing summary.json")
    args = parser.parse_args()
    try:
        result = score_review(args.review_dir)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"Review validation failed: {exc}\n")
    if not args.check_only:
        result["scored_at"] = datetime.now(timezone.utc).isoformat()
        destination = args.review_dir / "summary.json"
        destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        key: result[key]
        for key in ("review_status", "human_reviewed", "decision_counts", "precision", "precision_basis")
    }, indent=2))


if __name__ == "__main__":
    main()
