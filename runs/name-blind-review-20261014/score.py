"""Score unchanged blind responses; retain predeclared strict gate and diagnostics."""
import collections
import json

from review import ROOT, integrity, verify_response
from firmable_runtime.common import atomic_json, canonical
from firmable_runtime.execution import pointer_get


def main():
    protocol = json.loads((ROOT / "protocol.json").read_text())
    cost = json.loads((ROOT / "cost-receipt.json").read_text())
    assert cost["status"] == "finished", "Do not score an unfinished run."
    assert integrity(protocol)
    cases = {c["case_id"]: c for c in json.loads((ROOT / "blind-inputs.json").read_text())}
    index = {c["case_id"]: c for c in json.loads((ROOT / "private-index.json").read_text())}
    responses = {r["case_id"]: r for r in json.loads((ROOT / "responses.json").read_text())}
    rows = []
    for cid, case in cases.items():
        meta, result = index[cid], responses.get(cid, {})
        row = {"case_id": cid, "source": meta["source"], "role": meta["role"],
               "expected_control_verdict": meta["expected_control_verdict"],
               "status": result.get("status", "not_sent")}
        if result.get("status") != "complete":
            rows.append(row)
            continue
        answer = result["response"]
        validation = verify_response(case["request"], answer)
        assert validation == result["structural_check"]
        expected_indices = case["request"]["payload"]["name_mapping_indices"]
        reviews = answer["mapping_reviews"]
        target_reviews = [r for r in reviews if r["mapping_index"] in expected_indices]
        citation_checks = []
        for review in reviews:
            for number, citation in enumerate(review["evidence"]):
                item = {"mapping_index": review["mapping_index"], "citation_index": number,
                        "name_mapping": review["mapping_index"] in expected_indices,
                        "pointer": citation["pointer"], "exact": False}
                try:
                    val = pointer_get(case["request"]["payload"], citation["pointer"])
                    string = val if isinstance(val, str) else canonical(val).decode()
                    item["exact"] = bool(citation["quote"]) and citation["quote"] in string
                    if not item["exact"]:
                        try:
                            quoted = json.loads(citation["quote"])
                            if isinstance(val, dict) and isinstance(quoted, dict):
                                item["object_differences"] = {k: {"actual": val.get(k), "quoted": quoted.get(k)} for k in set(val) | set(quoted) if val.get(k) != quoted.get(k)}
                        except ValueError:
                            pass
                except Exception:
                    item["invalid_pointer"] = True
                citation_checks.append(item)
        name_unique_complete = sorted(r["mapping_index"] for r in target_reviews) == expected_indices
        name_verdicts = [r["verdict"] for r in target_reviews]
        diagnostic = None
        if name_unique_complete:
            diagnostic = "contradicted" if "contradicted" in name_verdicts else "insufficient_evidence" if "insufficient_evidence" in name_verdicts else "supported"
        row.update(case_verdict=answer["case_verdict"], strict_check=validation,
                   control_verdict_match=(answer["case_verdict"] == meta["expected_control_verdict"]) if meta["role"] != "target" else None,
                   diagnostic_name_verdict=diagnostic, name_mapping_indices=expected_indices,
                   extra_mapping_indices=[r["mapping_index"] for r in reviews if r["mapping_index"] not in expected_indices],
                   citations=citation_checks, name_reviews=target_reviews)
        rows.append(row)
    controls = [r for r in rows if r["role"] != "target"]
    targets = [r for r in rows if r["role"] == "target"]
    strict_gate = len(controls) == 6 and all(r.get("control_verdict_match") and r.get("strict_check", {}).get("valid") for r in controls)
    scored = {"protocol_gate_passed": strict_gate,
              "protocol_gate_limitation": "Do not promote semantic disagreements when the predeclared strict control gate fails. Post-hoc name-only projection is descriptive only and does not repair raw responses or redefine the gate.",
              "strict_complete_cases": sum(r.get("strict_check", {}).get("valid", False) for r in rows),
              "completed_cases": sum(r["status"] == "complete" for r in rows),
              "control_verdict_matches": sum(r.get("control_verdict_match", False) for r in controls),
              "control_cases": len(controls),
              "strict_control_passes": sum(r.get("control_verdict_match") and r.get("strict_check", {}).get("valid", False) for r in controls),
              "control_false_accepts_by_verdict": sum(r["role"] == "negative_control" and r.get("case_verdict") == "supported" for r in controls),
              "control_false_rejects_by_verdict": sum(r["role"] == "positive_control" and r.get("case_verdict") != "supported" for r in controls),
              "target_name_verdict_counts_descriptive": dict(collections.Counter(r.get("diagnostic_name_verdict", "unavailable") for r in targets)),
              "target_potential_false_rejections_by_verdict": [r["source"] for r in targets if r.get("diagnostic_name_verdict") == "supported"],
              "confirmed_false_rejection_rate": None,
              "human_gold": False, "runtime_integrity_unchanged": integrity(protocol),
              "cost": {k: cost[k] for k in ["calls", "cost_usd", "cost_complete", "wall_seconds", "stop_reason"]},
              "cases": rows}
    atomic_json(ROOT / "scores.json", scored)
    print(json.dumps({k: v for k, v in scored.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
