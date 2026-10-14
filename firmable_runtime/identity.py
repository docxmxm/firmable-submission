"""Conservative cross-source identity links and evidence-ranked profile views.

Linking requires shared qualified identifiers and rejects pair conflicts. Profile
selection and withdrawal retain field provenance and expose unresolved ties.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from datetime import datetime

from .common import Failure, canonical, digest
from .engine import ENUMS, canonical_valid, parse_typed_identifier

IDENTITY_VERSION = "common-anchor-0.2-subject-kind"
PROFILE_VERSION = "evidence-order-0.2-informative"
MAX_ID_BUCKET = 200


def _tuple(observation):
    return observation["source_id"], observation["source_record_id"]


def _normal_name(value):
    words = set(
        re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", value).lower())
    )
    return words - {"pty", "ltd", "limited", "proprietary", "the", "and"}


def _subject_kind_evidence(observation):
    """Conservative subject conflicts, not an inferred identifier owner or type mapping."""
    evidence = []
    entity_type = observation["values"].get("entity.entity_type")
    if entity_type in ("company", "sole_trader"):
        evidence.append(
            {
                "kind": "company" if entity_type == "company" else "person",
                "basis": "canonical_entity_type",
                "field": "entity.entity_type",
                "value": entity_type,
            }
        )
    raw = observation.get("raw_record")
    if not isinstance(raw, dict):
        return evidence
    # An arbitrary Individual cell or a contact's type does not describe this
    # observation's subject. Require a licensing holder/name/number context.
    labels = [
        (
            key,
            " ".join(
                re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", key).lower())
            ),
            value,
        )
        for key, value in raw.items()
        if isinstance(key, str)
        and isinstance(value, (str, int))
        and not isinstance(value, bool)
        and str(value).strip()
    ]
    holder_names = [
        key
        for key, label, _ in labels
        if re.fullmatch(r"(?:licen[cs]ee|licen[cs]e holder) name", label)
    ]
    licence_numbers = [
        key
        for key, label, _ in labels
        if re.fullmatch(
            r"(?:licen[cs]e|licen[cs]ee|licen[cs]e holder) (?:number|no)", label
        )
    ]
    if not holder_names or not licence_numbers:
        return evidence
    for key, label, value in labels:
        if not re.fullmatch(
            r"(?:licen[cs]e|licen[cs]ee|licen[cs]e holder) type(?: desc| description)?",
            label,
        ):
            continue
        kind = {
            "individual": "person",
            "natural person": "person",
            "sole trader": "person",
            "company": "company",
            "corporation": "company",
        }.get(str(value).strip().lower())
        if kind:
            evidence.append(
                {
                    "kind": kind,
                    "basis": "explicit_licence_holder_type",
                    "field": key,
                    "value": value,
                    "holder_name_fields": sorted(holder_names),
                    "licence_number_fields": sorted(licence_numbers),
                    "limitation": "Identifies the licensed subject; does not establish ownership of an ABN in the same row.",
                }
            )
    return evidence


def _record_features(observations):
    records = {}
    for observation in observations:
        key = _tuple(observation)
        feature = records.setdefault(
            key,
            {
                "ids": defaultdict(set),
                "names": set(),
                "observations": [],
                "subject_kind_evidence": [],
            },
        )
        feature["observations"].append(observation)
        for item in _subject_kind_evidence(observation):
            if item not in feature["subject_kind_evidence"]:
                feature["subject_kind_evidence"].append(item)
        for field, value in observation["values"].items():
            values = value if isinstance(value, list) else [value]
            for val in values:
                if field in {
                    "entity.abn",
                    "entity.acn",
                    "entity.nzbn",
                } and canonical_valid(field, val):
                    # A canonical checksum alone is insufficient if callers
                    # supplied no immutable raw evidence for this observation.
                    if not isinstance(observation.get("raw_record"), dict):
                        feature["id_evidence_problem"] = True
                        continue

                    def raw_scalars(item):
                        if isinstance(item, dict):
                            for child in item.values():
                                yield from raw_scalars(child)
                        elif isinstance(item, list):
                            for child in item:
                                yield from raw_scalars(child)
                        elif isinstance(item, (str, int)) and not isinstance(
                            item, bool
                        ):
                            yield str(item)

                    supported = {
                        parse_typed_identifier(raw, field.split(".")[1])
                        if field in {"entity.abn", "entity.acn"}
                        else re.sub(r"[\s-]", "", raw)
                        for raw in raw_scalars(observation["raw_record"])
                    }
                    if val not in supported:
                        feature["id_evidence_problem"] = True
                        continue
                    feature["ids"][field].add(val)
                if field in {"entity.legal_name", "entity.trading_name"} and isinstance(
                    val, str
                ):
                    feature["names"].add(val)
    return records


def _pair_reason(left, right, anchor):
    if left.get("id_evidence_problem") or right.get("id_evidence_problem"):
        return "canonical_identifier_not_supported_by_raw"
    for field in ("entity.abn", "entity.acn", "entity.nzbn"):
        a, b = left["ids"].get(field, set()), right["ids"].get(field, set())
        if len(a) > 1 or len(b) > 1 or (a and b and a != b):
            return "conflicting_valid_" + field.split(".")[1]
    if anchor[1] not in left["ids"].get(anchor[0], set()) or anchor[1] not in right[
        "ids"
    ].get(anchor[0], set()):
        return "no_shared_anchor"
    kinds = [
        {item["kind"] for item in feature.get("subject_kind_evidence", [])}
        for feature in (left, right)
    ]
    if any(len(group) > 1 for group in kinds):
        return "inconsistent_record_subject_kind"
    if kinds[0] and kinds[1] and kinds[0] != kinds[1]:
        return "person_company_subject_conflict"
    if left["names"] and right["names"]:
        pairs = [
            (_normal_name(a), _normal_name(b))
            for a in left["names"]
            for b in right["names"]
        ]
        if all(a and b and not a.intersection(b) for a, b in pairs):
            return "incompatible_names_need_review"
    return None


def link_records(
    observations, threshold=0.99, *, policy_version=IDENTITY_VERSION, exact_names=False
):
    if threshold != 0.99:
        raise Failure(
            "validation_error", "This frozen policy supports threshold 0.99 only"
        )
    records = _record_features(observations)
    anchors = defaultdict(set)
    for key, feature in records.items():
        signature = tuple(
            sorted(
                tuple(sorted(_normal_name(name)))
                for name in feature["names"]
                if _normal_name(name)
            )
        )
        if exact_names and not signature:
            continue
        for field, values in feature["ids"].items():
            for value in values:
                anchor = (field, value, signature) if exact_names else (field, value)
                anchors[anchor].add(key)
    candidate_groups, decisions = [], []
    rejection_reasons, cross_anchors = defaultdict(set), defaultdict(set)
    for anchor, members in sorted(anchors.items()):
        members = sorted(members)
        if len(members) > MAX_ID_BUCKET:
            raise Failure(
                "resource_limit",
                "Qualified identifier bucket exceeds bounded pair-check capacity",
                bucket_size=len(members),
                maximum=MAX_ID_BUCKET,
            )
        if len({key[0] for key in members}) < 2:
            continue
        for key in members:
            cross_anchors[key].add(anchor)
        failures = []
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                reason = _pair_reason(records[left], records[right], anchor)
                if reason:
                    failures.append(reason)
                    rejection_reasons[left].add(reason)
                    rejection_reasons[right].add(reason)
                if left[0] != right[0]:
                    decisions.append(
                        {
                            "left": list(left),
                            "right": list(right),
                            "anchor": list(anchor),
                            "accepted": reason is None,
                            "reason": reason or "shared_qualified_anchor",
                            "link_confidence": 0.99 if reason is None else 0.0,
                            "support": [list(left), list(right)],
                        }
                    )
                    if reason in {
                        "person_company_subject_conflict",
                        "inconsistent_record_subject_kind",
                    }:
                        decisions[-1]["subject_kind_evidence"] = {
                            "left": copy.deepcopy(
                                records[left]["subject_kind_evidence"]
                            ),
                            "right": copy.deepcopy(
                                records[right]["subject_kind_evidence"]
                            ),
                        }
        if not failures:
            candidate_groups.append((anchor, set(members)))
        else:
            for key in members:
                rejection_reasons[key].add("anchor_group_rejected_by_pair_conflict")
    # Overlapping distinct memberships are ambiguous. Do not make a transitive
    # union of ABN and ACN edges, or assign one record to two active entities.
    ambiguous = set()
    for index, (_, members) in enumerate(candidate_groups):
        for _, other in candidate_groups[index + 1 :]:
            if members != other and members & other:
                ambiguous.update(members & other)
    groups, seen_members = [], set()
    for anchor, members in candidate_groups:
        members -= ambiguous
        signature = tuple(sorted(members))
        if len({key[0] for key in members}) < 2:
            for key in members:
                rejection_reasons[key].add(
                    "cross_source_anchor_lost_after_ambiguity_abstention"
                )
            continue
        if signature in seen_members:
            continue
        seen_members.add(signature)
        groups.append((anchor, members))
    links, linked, proposed_pairs = [], set(), set()
    for anchor, members in groups:
        entity_key = (
            "entity:" + digest({"policy": policy_version, "anchor": anchor})[:24]
        )
        members = sorted(members)
        linked.update(members)
        pairs = []
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                pair = (left, right)
                if left[0] != right[0] and pair not in proposed_pairs:
                    proposed_pairs.add(pair)
                    pairs.append(
                        {
                            "left": list(left),
                            "right": list(right),
                            "link_confidence": 0.99,
                            "anchor": list(anchor),
                            "support": [list(left), list(right)],
                        }
                    )
        links.append(
            {
                "entity_key": entity_key,
                "members": [
                    {"source_id": s, "source_record_id": r} for s, r in members
                ],
                "anchor": list(anchor),
                "link_confidence": 0.99,
                "pairs": pairs,
                "evidence": "Every member shares the qualified same-type anchor; all pairs checked for conflicts",
            }
        )
    for decision in decisions:
        if (
            tuple(decision["left"]) in ambiguous
            or tuple(decision["right"]) in ambiguous
        ):
            decision["accepted"], decision["reason"] = (
                False,
                "ambiguous_multi_anchor_membership",
            )
    unlinked = []
    for key in sorted(set(records) - linked):
        feature = records[key]
        reasons = set(rejection_reasons[key])
        if feature.get("id_evidence_problem"):
            reasons.add("canonical_identifier_not_supported_by_raw")
        for field, values in feature["ids"].items():
            if len(values) > 1:
                reasons.add("conflicting_valid_" + field.split(".")[1])
        if key in ambiguous:
            reason = "ambiguous_multi_anchor_membership"
            reasons.add(reason)
        elif reasons:
            specific = sorted(reasons - {"anchor_group_rejected_by_pair_conflict"})
            reason = (
                specific[0] if specific else "anchor_group_rejected_by_pair_conflict"
            )
        elif not feature["ids"]:
            reason = "no_qualified_identifier"
        elif exact_names and not feature["names"]:
            reason = "shadow_policy_requires_supported_name"
        elif not cross_anchors[key]:
            reason = "qualified_identifier_without_cross_source_anchor"
        else:
            reason = "no_active_cross_source_membership_after_abstention"
        reasons.add(reason)
        unlinked.append(
            {
                "source_id": key[0],
                "source_record_id": key[1],
                "reason": reason,
                "reasons": sorted(reasons),
                "qualified_identifier_count": sum(
                    len(values) for values in feature["ids"].values()
                ),
                "cross_source_anchor_count": len(cross_anchors[key]),
            }
        )
        if feature["subject_kind_evidence"]:
            unlinked[-1]["subject_kind_evidence"] = copy.deepcopy(
                feature["subject_kind_evidence"]
            )
    return {
        "links": links,
        "unlinked": unlinked,
        "policy_version": policy_version,
        "threshold": threshold,
        "candidate_decisions": decisions,
        "observations_hash": digest(observations),
        "membership_version": digest(links),
    }


def _claim_order(claim):
    return (
        -claim["source_reliability"],
        -claim["field_confidence"],
        claim["source_id"],
        claim["source_record_id"],
        canonical(claim["value"]),
    )


def _profile_field(claims):
    claims = sorted(claims, key=_claim_order)
    first = claims[0]
    best = [
        c
        for c in claims
        if (c["source_reliability"], c["field_confidence"])
        == (first["source_reliability"], first["field_confidence"])
    ]
    reason = "source_reliability_then_field_confidence"
    if len(best) > 1:
        if all(c["observed_at_basis"] == "source_stated" for c in best):
            try:
                times = {
                    c["observed_at"]: datetime.fromisoformat(
                        c["observed_at"].replace("Z", "+00:00")
                    )
                    for c in best
                }
                if any(t.tzinfo is None for t in times.values()):
                    raise ValueError("naive source time")
                newest = max(times.values())
                best = [c for c in best if times[c["observed_at"]] == newest]
                reason = (
                    "source_reliability_then_field_confidence_then_source_stated_time"
                )
            except (ValueError, TypeError):
                reason = "deterministic_tie;source_times_incomparable"
        else:
            reason = "deterministic_tie;proxy_times_do_not_establish_recency"
        if len(best) > 1:
            reason += ";evidence_did_not_resolve_tie"
    winner = sorted(best, key=_claim_order)[0]
    same = [c for c in claims if c["value"] == winner["value"]]
    losers = [
        {**c, "reason_rejected": reason}
        for c in claims
        if c["value"] != winner["value"]
    ]
    return {
        "value": winner["value"],
        "confidence": winner["field_confidence"],
        "provenance": same,
        "conflicts": losers,
        "selection_reason": reason,
    }


def build_profile_view(observations, linking):
    by_record = defaultdict(list)
    for observation in observations:
        by_record[_tuple(observation)].append(observation)
    profiles = []
    for link in sorted(linking["links"], key=lambda item: item["entity_key"]):
        claims, surviving = defaultdict(list), []
        for member in link["members"]:
            key = _tuple(member)
            for observation in by_record[key]:
                surviving.append(observation)
                for field, value in observation["values"].items():
                    for val in value if isinstance(value, list) else [value]:
                        if not canonical_valid(field, val):
                            continue
                        # Unknown enum values are retained observations, not
                        # informative profile claims or competing conflicts.
                        if field in ENUMS and val == "unknown":
                            continue
                        claims[field].append(
                            {
                                "value": val,
                                "source_id": key[0],
                                "source_record_id": key[1],
                                "source_reliability": observation["source_reliability"],
                                "field_confidence": observation["field_confidence"][
                                    field
                                ],
                                "observed_at": observation["observed_at"],
                                "observed_at_basis": observation["observed_at_basis"],
                            }
                        )
        if not surviving:
            continue
        fields = {
            field: _profile_field(items) for field, items in sorted(claims.items())
        }
        profiles.append(
            {
                "entity_key": link["entity_key"],
                "fields": fields,
                "source_count": len({o["source_id"] for o in surviving}),
                "record_count": len({_tuple(o) for o in surviving}),
                "selection_policy_version": PROFILE_VERSION,
            }
        )
    return {
        "profiles": profiles,
        "observations_hash": digest(observations),
        "links_hash": digest(linking),
        "selection_policy_version": PROFILE_VERSION,
    }


def withdraw_view(profiles, observations, linking, source_id):
    surviving = [copy.deepcopy(o) for o in observations if o["source_id"] != source_id]
    result = build_profile_view(surviving, linking)
    before = {p["entity_key"]: p for p in profiles}
    after = {p["entity_key"]: p for p in result["profiles"]}
    delta, lost, changed, affected = [], 0, 0, 0
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new != old:
            affected += 1
        old_fields = old["fields"]
        new_fields = new["fields"] if new else {}
        lost_fields = sorted(set(old_fields) - set(new_fields))
        changed_fields = sorted(
            field
            for field in set(old_fields) & set(new_fields)
            if old_fields[field]["value"] != new_fields[field]["value"]
        )
        lost += len(lost_fields)
        changed += len(changed_fields)
        if new != old:
            delta.append(
                {
                    "entity_key": key,
                    "dropped": new is None,
                    "fields_lost": lost_fields,
                    "fields_changed": changed_fields,
                    "before_hash": digest(old),
                    "after_hash": digest(new),
                }
            )
    result.update(
        {
            "withdraw_source_id": source_id,
            "profiles_dropped": len(set(before) - set(after)),
            "profiles_affected": affected,
            "fields_lost": lost,
            "fields_changed": changed,
            "delta": delta,
            "identity_review_required": [
                link["entity_key"]
                for link in linking["links"]
                if any(m["source_id"] == source_id for m in link["members"])
            ],
            "identity_limitation": "Existing entity keys retained; source withdrawal does not revalidate identity",
        }
    )
    return result
