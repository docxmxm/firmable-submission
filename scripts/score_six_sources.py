"""Score one six-source development run offline against frozen, narrow labels.

Labels never enter inference. Missing or invalid output has no quality score;
coverage of unlabelled fields is not correctness or human approval.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from firmable_runtime.common import Failure, byte_hash, canonical, digest, strict_json
from firmable_runtime.execution import discover, validate_observation


def read_json(path):
    return strict_json(path.read_bytes()) if path.exists() else None


def normalized(value):
    return " ".join(value.split()) if isinstance(value, str) else value


def informative(value):
    return value not in (None, "", "unknown")


def values(row):
    return {
        group + "." + field: value
        for group in ("entity", "address")
        for field, value in row.get(group, {}).items()
    }


def coverage(rows):
    present, nonempty, useful = Counter(), Counter(), Counter()
    for row in rows:
        for field, value in values(row).items():
            present[field] += 1
            nonempty[field] += normalized(value) not in (None, "")
            useful[field] += informative(normalized(value))
    return {
        "field_present": dict(present),
        "field_nonempty": dict(nonempty),
        "field_informative": dict(useful),
        "scope": "Coverage only; unknown is not informative; unlabelled values are not scored correct.",
    }


def input_records(project, source_id, labels):
    root = project / "data/assessment-inputs" / source_id / "evidence"
    original = Path(labels["evidence_root"])
    expected = {
        Path(path).relative_to(original).as_posix(): sha
        for path, sha in labels["evidence_file_sha256"].items()
    }
    actual = {
        path.relative_to(root).as_posix(): byte_hash(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise ValueError(
            "Native evidence bytes differ from the frozen scoring population"
        )
    tables, _, _, _ = discover(root, source_id)
    rows = {
        row["source_record_id"]: row
        for table in tables
        for row in table["records"]
        if row["source_record_id"] in labels["records"]
    }
    if set(rows) != set(labels["records"]) or any(
        digest(row["raw_record"]) != labels["records"][key]["raw_sha256"]
        for key, row in rows.items()
    ):
        raise ValueError(
            "Native reader does not reproduce the frozen identity/raw population"
        )
    return rows


def labelled_metrics(rows, labels):
    metrics, branches, abstention = {}, {}, {}
    unscored, mismatches = Counter(), []

    def bucket(mapping, field):
        return mapping.setdefault(
            field,
            {
                "opportunities": 0,
                "correct": 0,
                "missed": 0,
                "wrong_emitted": 0,
                "informative_emitted": 0,
            },
        )

    for row in rows:
        record_id = row["source_record_id"]
        label = labels["records"][record_id]
        emitted = values(row)
        for field, expected in label["expected"].items():
            value = normalized(emitted.get(field))
            useful = informative(value)
            correct = useful and value == normalized(expected["value"])
            outcome = "correct" if correct else "wrong_emitted" if useful else "missed"
            for item in (
                bucket(metrics, field),
                bucket(branches, field + "::" + expected["branch"]),
            ):
                item["opportunities"] += 1
                item[outcome] += 1
                item["informative_emitted"] += useful
            if not correct and len(mismatches) < 20:
                mismatches.append(
                    {
                        "source_record_id": record_id,
                        "field": field,
                        "outcome": outcome,
                        "expected": expected["value"],
                        "actual": value,
                    }
                )
        for field in label["abstention"]:
            item = abstention.setdefault(
                field, {"opportunities": 0, "abstained": 0, "unsupported_emitted": 0}
            )
            item["opportunities"] += 1
            item[
                "unsupported_emitted"
                if informative(normalized(emitted.get(field)))
                else "abstained"
            ] += 1
        for field, value in emitted.items():
            if (
                informative(normalized(value))
                and field not in label["expected"]
                and field not in label["abstention"]
            ):
                unscored[field] += 1
    for item in metrics.values():
        item["recall"] = item["correct"] / item["opportunities"]
        item["precision_within_labelled_opportunities"] = (
            item["correct"] / item["informative_emitted"]
            if item["informative_emitted"]
            else None
        )
    return {
        "metrics": metrics,
        "branches": branches,
        "abstention_checks": abstention,
        "unscored_informative_emissions": dict(unscored),
        "mismatches": mismatches,
    }


def score_source(project, run_root, source_id, labels):
    run = run_root / source_id
    result = {
        "source_id": source_id,
        "run": str(run),
        "native_record_count": labels["native_record_count"],
        "positive_opportunities": labels["positive_opportunities"],
        "positive_branches": labels["positive_branches"],
        "abstention_opportunities": labels["abstention_opportunities"],
        "status": "missing_output",
        "accuracy_status": "N/A",
        "metrics": None,
        "coverage": None,
        "guard_errors": {},
    }
    try:
        native = input_records(project, source_id, labels)
    except (ValueError, Failure, OSError) as exc:
        result.update(status="input_binding_failed", error=str(exc))
        return result
    try:
        runner = read_json(run / "result.json") or {}
        onboard = read_json(run / "onboard.json") or {}
        extracted = read_json(run / "extraction.json") or {}
        config = read_json(run / "artifacts/final-config.json")
        result["workflow"] = {
            "runner_status": runner.get("status"),
            "error": runner.get("error"),
            "onboarding_status": onboard.get("status"),
            "task_status": extracted.get("task_status", onboard.get("task_status")),
            "candidate_status": onboard.get("candidate_status"),
            "execution_status": extracted.get("execution_status"),
            "extraction_error_count": len(
                extracted.get("report", {}).get("errors", [])
            ),
            "unresolved_requirements": extracted.get(
                "unresolved_requirements", onboard.get("unresolved_requirements")
            ),
        }
        result["configuration"] = {
            "exists": config is not None,
            "mapped_targets": sorted(
                {m["canonical_field"] for m in config["field_mappings"]}
            )
            if config
            else [],
            "mapping_count": len(config["field_mappings"]) if config else 0,
        }
        output = run / "observations.jsonl"
        result["output_exists"] = output.exists()
        if not output.exists():
            if runner.get("error"):
                result["status"] = "workflow_failed"
            return result
        payload = output.read_bytes()
        rows = [strict_json(line) for line in payload.splitlines() if line.strip()]
        result.update(output_sha256=byte_hash(payload), output_record_count=len(rows))
        guards, by_id, usable = Counter(), defaultdict(list), []
        for row in rows:
            try:
                validate_observation(row)
            except (Failure, TypeError, KeyError, AttributeError):
                guards["invalid_observation"] += 1
                continue
            usable.append(row)
            key = row["source_record_id"]
            by_id[key].append(row)
            if row["source_id"] != source_id:
                guards["wrong_source_id"] += 1
            if key not in native:
                guards["unknown_or_missing_record_id"] += 1
            elif digest(row["raw_record"]) != labels["records"][key]["raw_sha256"]:
                guards["raw_record_changed"] += 1
            elif row.get("native_locator") != native[key]["locator"]:
                guards["native_locator_changed"] += 1
        guards["duplicate_record_id"] += sum(len(items) - 1 for items in by_id.values())
        missing = len(set(native) - set(by_id))
        guards["missing_native_record_id"] += missing
        result.update(
            coverage=coverage(usable),
            missing_native_record_count=missing,
            guard_errors={k: v for k, v in guards.items() if v},
        )
        if result["guard_errors"]:
            result["status"] = "identity_guard_failed"
            return result
        if (
            runner.get("error")
            or extracted.get("execution_status") != "complete"
            or result["workflow"]["extraction_error_count"]
            or not config
            or onboard.get("candidate_status") != "validated"
        ):
            result["status"] = "workflow_failed"
            return result
        result.update(
            status="complete_output",
            accuracy_status="narrow_labels_only"
            if labels["positive_opportunities"] or labels["abstention_opportunities"]
            else "coverage_only",
        )
        result.update(labelled_metrics(rows, labels))
        return result
    except (ValueError, Failure, OSError, KeyError, TypeError, AttributeError) as exc:
        result.update(status="invalid_output", error=str(exc))
        return result


def score_run(run_root, project=PROJECT):
    label_path = project / "evidence/six-source-frozen-labels.json"
    labels = read_json(label_path)
    historical = read_json(project / "evidence/six-source-scores.json")
    previous = {
        r["source_id"]: r for r in historical["results"] if r["repetition"] == 2
    }
    results = [
        score_source(project, run_root, source, data)
        for source, data in sorted(labels["sources"].items())
    ]
    for result in results:
        old = previous[result["source_id"]]
        result["selected_r2_reference"] = {
            k: old.get(k)
            for k in (
                "status",
                "accuracy_status",
                "metrics",
                "coverage",
                "abstention_checks",
            )
        }
    return {
        "human_gold": False,
        "frozen_labels_sha256": byte_hash(label_path.read_bytes()),
        "scorer_sha256": byte_hash(Path(__file__).read_bytes()),
        "status_counts": dict(Counter(r["status"] for r in results)),
        "results": results,
        "limitation": "Single seen-source repetition, not stability or unseen generalisation. No pooled accuracy. Companies names389, current business names888 and SOR types8184 have positive labels; ATO/QBCC/ACT have coverage only. Missing/invalid output remains N/A with frozen denominators. Historical reference is a separate earlier run.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    if not root.is_dir():
        parser.error("run_root must be an existing directory")
    result = score_run(root)
    destination = root / "scores.json"
    destination.write_bytes(canonical(result) + b"\n")
    print(
        json.dumps(
            {"scores": str(destination), "status_counts": result["status_counts"]}
        )
    )


if __name__ == "__main__":
    main()
