from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

RULE_VERSION = "weekly-report-eval-v2"
CANDIDATE_PENDING_COVERAGE_RULE_VERSION = "weekly-report-eval-v2-pending-candidate-1"
SECTION_ALIASES = {
    "本周完成": ("本周完成", "已完成"),
    "进行中": ("进行中",),
    "风险与阻塞": ("风险与阻塞", "风险和阻塞", "风险"),
    "下周计划": ("下周计划", "下一周计划"),
    "待确认事项": ("待确认事项", "待确认", "需要确认"),
}

# Candidate-only, case-scoped equivalents. Keep these out of frozen fixtures so
# the locked Baseline remains reproducible with its original exact-match rules.
CANDIDATE_PENDING_ANCHORS: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "TEST-02": {
        "样本编码补录规则": (("样本编码", "补录", "确认"),),
    },
}


def dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_dataset_hash(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8"))
    for item in payload:
        attachment = item.get("attachment")
        if attachment:
            asset = (path.parent / attachment).resolve()
            if not asset.is_relative_to((path.parent / "assets").resolve().parent) or not asset.is_file():
                raise ValueError(f"Missing or unsafe dataset attachment: {attachment}")
            digest.update(attachment.encode("utf-8"))
            digest.update(asset.read_bytes())
    return digest.hexdigest()


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) > 10:
        raise ValueError("An evaluation dataset must be a list of at most 10 cases.")
    seen: set[str] = set()
    required = {
        "id", "split", "task_name", "notes", "report_period", "attachment",
        "required_sections", "required_facts", "forbidden_claims",
        "expected_classification", "revision_instruction",
    }
    for case in payload:
        if not required.issubset(case):
            raise ValueError(f"{case.get('id', '<unknown>')} is missing required fields.")
        if case["id"] in seen:
            raise ValueError(f"Duplicate evaluation case id: {case['id']}")
        seen.add(case["id"])
    return payload


def parse_judge_json(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"valid": False, "error": f"Malformed Judge JSON: {exc.__class__.__name__}"}
    if not isinstance(result, dict):
        return {"valid": False, "error": "Judge JSON must be an object."}
    scores = result.get("groundedness"), result.get("completeness")
    if any(not isinstance(value, (int, float)) or not 0 <= value <= 5 for value in scores):
        return {"valid": False, "error": "Judge scores must be numeric values from 0 to 5."}
    return {"valid": True, **result}


def score_report(
    case: dict[str, Any],
    report: str,
    *,
    runtime: dict[str, Any] | None = None,
    include_candidate_pending_coverage: bool = False,
) -> dict[str, Any]:
    runtime = runtime or {}
    sections = _section_blocks(report)
    required_sections = case["required_sections"]
    facts = case["required_facts"]
    fact_anchors = case.get("required_fact_anchors", {})
    found_facts = [
        fact for fact in facts
        if fact["text"].casefold() in report.casefold()
        or any(all(term.casefold() in report.casefold() for term in pattern)
               for pattern in fact_anchors.get(fact["text"], []))
    ]
    classifications = case["expected_classification"]
    classification_anchors = case.get("classification_anchors", {})
    classification_results = {}
    for fact, targets in classifications.items():
        section_text = "\n".join(sections.get(section, "") for section in targets).casefold()
        patterns = classification_anchors.get(fact) or [[fact]]
        classification_results[fact] = any(
            all(anchor.casefold() in section_text for anchor in pattern)
            for pattern in patterns
        )
    forbidden_hits = [claim for claim in case["forbidden_claims"] if claim.casefold() in report.casefold()]
    revision = case.get("revision_check") or {}
    revision_checked = bool(case.get("revision_instruction") and revision)
    revision_ok = all(term.casefold() in report.casefold() for term in revision.get("must_include", []))
    revision_ok = revision_ok and not any(term.casefold() in report.casefold() for term in revision.get("must_exclude", []))
    period = case.get("report_period")
    route_correct = runtime.get("material_understanding") == bool(case.get("attachment")) if "material_understanding" in runtime else None
    sections_correct = sum(section in sections for section in required_sections)
    result = {
        "task_success": runtime.get("task_status") in {"waiting", "completed"} if "task_status" in runtime else None,
        "route_correct": route_correct,
        "section_completeness": sections_correct / max(len(required_sections), 1),
        "sections_found": [section for section in required_sections if section in sections],
        "period_accuracy": bool(period and period in report) if period else None,
        "critical_fact_recall": len(found_facts) / max(len(facts), 1),
        "required_facts_found": [fact["text"] for fact in found_facts],
        "status_classification_accuracy": sum(classification_results.values()) / max(len(classification_results), 1),
        "classification_results": classification_results,
        "forbidden_claim_hits": forbidden_hits,
        "revision_success": revision_ok if revision_checked else None,
        "revision_checked": revision_checked,
        "items_to_confirm_coverage": _pending_coverage(case, sections),
        "artifact_success": bool(runtime.get("artifact_success")) if "artifact_success" in runtime else None,
    }
    if include_candidate_pending_coverage:
        result["candidate_pending_coverage"] = score_pending_coverage_candidate(case, report)
    return result


def _section_blocks(markdown: str) -> dict[str, str]:
    lines = markdown.splitlines()
    headers: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        normalized = re.sub(r"[*_`#]", "", line).strip()
        normalized = re.sub(r"^(?:第\s*)?[0-9一二三四五六七八九十]+\s*[.、:：)）-]*\s*", "", normalized)
        normalized = re.sub(r"^[（(][0-9一二三四五六七八九十]+[）)]\s*", "", normalized)
        normalized = re.sub(r"\s+", "", normalized)
        for section, aliases in SECTION_ALIASES.items():
            if any(normalized.startswith(alias) for alias in aliases):
                headers.append((index, section))
                break
    result: dict[str, str] = {}
    for pos, (start, section) in enumerate(headers):
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        result[section] = "\n".join(lines[start + 1:end])
    return result


def _pending_coverage(case: dict[str, Any], sections: dict[str, str]) -> float:
    expected = case.get("pending_items", [])
    text = sections.get("待确认事项", "").casefold()
    if not expected:
        return 1.0
    anchors = case.get("pending_anchors", {})
    matches = 0
    for item in expected:
        patterns = anchors.get(item) or [[item]]
        matches += any(all(term.casefold() in text for term in pattern) for pattern in patterns)
    return matches / len(expected)


def score_pending_coverage_candidate(
    case: dict[str, Any],
    report: str,
    *,
    candidate_anchors: dict[str, dict[str, tuple[tuple[str, ...], ...]]] | None = None,
) -> dict[str, Any]:
    """Return opt-in candidate coverage with minimal, non-content diagnostics."""
    sections = _section_blocks(report)
    text = sections.get("待确认事项", "").casefold()
    expected = case.get("pending_items", [])
    fixture_anchors = case.get("pending_anchors", {})
    all_candidate_anchors = candidate_anchors if candidate_anchors is not None else CANDIDATE_PENDING_ANCHORS
    case_candidate_anchors = all_candidate_anchors.get(case.get("id", ""), {})
    items: list[dict[str, Any]] = []

    for item in expected:
        exact_match = item.casefold() in text
        fixture_patterns = fixture_anchors.get(item, [])
        fixture_match = next(
            (pattern for pattern in fixture_patterns if all(term.casefold() in text for term in pattern)),
            None,
        )
        candidate_patterns = case_candidate_anchors.get(item, ())
        candidate_match = next(
            (pattern for pattern in candidate_patterns if all(term.casefold() in text for term in pattern)),
            None,
        )
        if exact_match:
            match_type = "exact_item"
            matched_anchors = [item]
        elif fixture_match:
            match_type = "fixture_anchor"
            matched_anchors = list(fixture_match)
        elif candidate_match:
            match_type = "candidate_anchor"
            matched_anchors = list(candidate_match)
        else:
            match_type = "none"
            matched_anchors = []

        items.append({
            "item": item,
            "covered": match_type != "none",
            "match_type": match_type,
            "matched_anchors": matched_anchors,
            "unmatched_candidate_patterns": [list(pattern) for pattern in candidate_patterns]
            if match_type == "none" else [],
            "reason": None if match_type != "none" else "No exact, fixture, or candidate anchor matched in 待确认事项.",
        })

    covered = sum(item["covered"] for item in items)
    return {
        "rule_version": CANDIDATE_PENDING_COVERAGE_RULE_VERSION,
        "coverage": covered / max(len(expected), 1),
        "items": items,
    }
