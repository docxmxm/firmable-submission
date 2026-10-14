"""Prepare a reproducible, unlabelled sample of current proposed links."""

import csv
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs"
    destination = output / "human-review"
    if destination.exists():
        raise SystemExit("Review directory already exists; preserve existing decisions.")
    links_bytes = (output / "links.json").read_bytes()
    links = json.loads(links_bytes)
    observations = {}
    for path in sorted((output / "observations").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            key = (record["source_id"], record["source_record_id"])
            assert key not in observations
            observations[key] = record
    pairs = {}
    for entity in links["links"]:
        for pair in entity["pairs"]:
            endpoints = sorted([pair["left"], pair["right"]])
            identity = json.dumps(endpoints, separators=(",", ":"))
            pair_id = hashlib.sha256(identity.encode()).hexdigest()
            assert pair_id not in pairs
            pairs[pair_id] = {"pair_id": pair_id, "entity_key": entity["entity_key"], **pair}
    seed = "firmable-pre-scope-r2-review-20261014"
    selected = sorted(
        pairs,
        key=lambda pair_id: hashlib.sha256((seed + ":" + pair_id).encode()).hexdigest(),
    )[:50]
    assert len(selected) == 50
    destination.mkdir()
    with (destination / "samples.jsonl").open("w") as handle:
        for pair_id in selected:
            pair = pairs[pair_id]
            sample = {
                **pair,
                "left_observation": observations[tuple(pair["left"])],
                "right_observation": observations[tuple(pair["right"])],
            }
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with (destination / "reviews.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pair_id", "decision", "reviewer", "notes"])
        writer.writeheader()
        writer.writerows({"pair_id": pair_id} for pair_id in selected)
    summary = {
        "seed": seed,
        "sampling": "Ascending SHA256(seed + ':' + pair_id), without replacement",
        "links_sha256": hashlib.sha256(links_bytes).hexdigest(),
        "proposed_pair_population": len(pairs),
        "sample_size": 50,
        "human_reviewed": 0,
        "precision": None,
        "threshold": links["threshold"],
        "policy_version": links["policy_version"],
        "limitation": "Pending human decisions. No labels inherited from earlier outputs.",
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
