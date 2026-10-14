"""Offline comparison of frozen cohort outputs; labels stay evaluator-only."""

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

from score_six_sources import PROJECT, coverage, informative, score_source, values


def read(path):
    return json.loads(path.read_text())


def cohort(root):
    summary = read(root / "summary.json")
    labels = read(PROJECT / "evidence/six-source-frozen-labels.json")["sources"]
    spec = importlib.util.spec_from_file_location(
        "reserve_audit",
        PROJECT / "runs/two-reserve-sources-20261014/audit/verify_results.py",
    )
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    results = []
    for outcome in summary["results"]:
        source = outcome["source_id"]
        directory = root / "execution" / source
        output = directory / "observations.jsonl"
        row = {
            "source_id": source,
            "status": outcome["status"],
            "task_status": outcome.get("task_status"),
            "usage": summary["per_source"][source],
            "seconds": outcome["wall_seconds"],
        }
        if source in labels:
            score = score_source(PROJECT, root / "execution", source, labels[source])
            row["audit"] = {
                k: score.get(k)
                for k in [
                    "status",
                    "native_record_count",
                    "output_record_count",
                    "guard_errors",
                    "metrics",
                    "abstention_checks",
                    "mismatches",
                ]
            }
        elif output.exists():
            score = auditor.audit(
                source, directory / "artifacts/final-config.json", output
            )
            row["audit"] = {
                k: score[k]
                for k in [
                    "native_rows",
                    "output_rows",
                    "output_rows_by_grain",
                    "issue_counts",
                    "semantic_review_counts",
                ]
            }
            row["audit"]["missing_native_locators"] = len(
                score["native_locators_absent_from_output"]
            )
            row["audit"]["duplicate_locators"] = len(score["repeated_native_locators"])
        if output.exists():
            observations = [
                json.loads(line) for line in output.read_text().splitlines()
            ]
            row["coverage"] = coverage(observations)["field_informative"]
            row["empty_rows"] = sum(
                not any(
                    v not in (None, "", "unknown")
                    for g in ["entity", "address"]
                    for v in r.get(g, {}).values()
                )
                for r in observations
            )
            row["by_grain"] = {}
            grains = {r["native_locator"].get("sheet", "table") for r in observations}
            for grain in sorted(grains):
                part = [
                    r
                    for r in observations
                    if r["native_locator"].get("sheet", "table") == grain
                ]
                row["by_grain"][grain] = {
                    "rows": len(part),
                    "coverage": coverage(part)["field_informative"],
                }
        else:
            row["coverage"] = None
        plan = directory / "artifacts/host-subset-repair-plan.json"
        if plan.exists():
            saved = read(plan)
            row["repair"] = {
                k: saved.get(k)
                for k in [
                    "candidate_id",
                    "source_candidate_id",
                    "trigger",
                    "removed_enum_entries",
                    "removed_output_branches",
                    "rebound_evidence",
                    "excluded_mapping_indices",
                ]
            }
            probe = (
                directory / "artifacts" / ("probe-" + saved["candidate_id"] + ".json")
            )
            if probe.exists():
                row["repair"]["probe_errors"] = dict(
                    Counter(
                        e.get("reason_code", "unknown")
                        for e in read(probe)["report"]["errors"]
                    )
                )
        row["retention_refusals"] = len(list((directory / "artifacts").glob("selection-retention-*.json")))
        results.append(row)
    return {
        "runtime": read(root / "protocol.json")["runtime_fingerprint"],
        "usage": {
            k: summary.get(k)
            for k in [
                "model_calls",
                "reported_cost_usd",
                "wall_seconds",
                "cost_complete",
                "stop_reason",
                "integrity_end",
            ]
        },
        "sources": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--baseline-run", type=Path)
    args = parser.parse_args()
    roots = {"before": args.baseline_run or args.root / "before", "after": args.root / "after"}
    result = {
        "human_gold": False,
        "causal_limitation": "One fresh trajectory per arm; input/model/policy held fixed. Differences are not statistical reliability or causal proof.",
        "arms": {
            arm: cohort(directory)
            for arm, directory in roots.items()
            if (directory / "summary.json").exists()
        },
    }
    result["paired_value_changes"] = {}
    for source in sorted({s["source_id"] for arm in result["arms"].values() for s in arm["sources"]}):
        paths = [roots[arm] / "execution" / source / "observations.jsonl" for arm in ["before", "after"]]
        if not all(p.exists() for p in paths):
            continue
        paired = [{r["source_record_id"]: r for r in (json.loads(line) for line in p.read_text().splitlines())} for p in paths]
        if set(paired[0]) != set(paired[1]):
            result["paired_value_changes"][source] = {"status": "record_population_mismatch"}
            continue
        changes = Counter()
        for key in paired[0]:
            before, after = [values(rows[key]) for rows in paired]
            for field in set(before) | set(after):
                a, b = before.get(field), after.get(field)
                if informative(a) and informative(b):
                    kind = "retained_equal" if a == b else "changed_value"
                elif informative(a):
                    kind = "lost_informative"
                elif informative(b):
                    kind = "gained_informative"
                else:
                    continue
                changes[(field, kind)] += 1
        result["paired_value_changes"][source] = {
            field: {kind: count for (f, kind), count in changes.items() if f == field}
            for field in sorted({f for f, _ in changes})
        }
    (args.root / "comparison.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    for arm, data in result["arms"].items():
        print(arm, data["runtime"], data["usage"])
        for s in data["sources"]:
            print(s["source_id"], s["status"], s["coverage"], "repair", s.get("repair"))


if __name__ == "__main__":
    main()
