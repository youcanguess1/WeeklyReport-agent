from __future__ import annotations

from typing import Any

from app.memory.evidence_memory import EvidenceMemory
from app.memory.memory_tool import MemoryTool
from app.schemas.review import ReviewResult
from app.schemas.report import ReportSection, WeeklyReportDocument
from app.services.artifact_service import ArtifactService
from app.workflows.graph_state import TaskState


def build_memory_evolution_context(
    *,
    artifact_service: ArtifactService,
    memory_tool: MemoryTool,
    evidence_memory: EvidenceMemory,
    task_id: str,
    final_state: TaskState,
) -> dict[str, Any]:
    task_index = artifact_service.get_task_index(task_id)
    report_versions = artifact_service.list_report_versions(task_id)
    report_diffs = _build_report_diffs(report_versions)
    timeline = _build_timeline(memory_tool, task_id)
    relevant_section_ids = _collect_relevant_section_ids(final_state, report_diffs, timeline)
    return {
        "task_overview": _build_task_overview(task_index, report_versions, final_state),
        "report_diffs": report_diffs,
        "chronological_event_timeline": timeline,
        "section_evidence_summaries": _build_section_evidence_summaries(
            evidence_memory,
            task_id,
            relevant_section_ids,
        ),
    }


def _build_task_overview(
    task_index: dict[str, Any],
    report_versions: list[dict[str, Any]],
    final_state: TaskState,
) -> dict[str, Any]:
    summary = None
    if final_state.latest_review_result is not None:
        summary = final_state.latest_review_result.summary
    return {
        "task_id": final_state.task_id,
        "task_name": task_index["task_name"],
        "task_type": task_index["task_type"],
        "final_status": final_state.task_status.value,
        "writer_round": final_state.writer_round,
        "review_round": final_state.review_round,
        "supplement_round": final_state.supplement_round,
        "latest_report_version": report_versions[-1]["version"] if report_versions else None,
        "summary": summary,
    }


def _build_report_diffs(report_versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs = []
    for previous_version, current_version in zip(report_versions, report_versions[1:]):
        previous = WeeklyReportDocument.model_validate(previous_version["structured_snapshot"])
        current = WeeklyReportDocument.model_validate(current_version["structured_snapshot"])
        changes = []
        for section in ReportSection:
            before = getattr(previous, section.value)
            after = getattr(current, section.value)
            if before != after:
                changes.append({
                    "section_id": section.value,
                    "before": before,
                    "after": after,
                    "added_items": [item for item in after if item not in before],
                    "removed_items": [item for item in before if item not in after],
                })
        diffs.append({
            "from_version": previous_version["version"],
            "to_version": current_version["version"],
            "change_note": current_version["change_note"],
            "section_changes": changes,
        })
    return diffs


def _build_timeline(memory_tool: MemoryTool, task_id: str) -> list[dict[str, Any]]:
    sessions = sorted(
        memory_tool.list_agent_sessions(task_id),
        key=lambda item: (
            str(item["started_at"]),
            int(item["round_index"]),
            int(item["attempt_index"]),
        ),
    )
    timeline: list[dict[str, Any]] = []
    for session in sessions:
        records = memory_tool.load_records(str(session["agent_session_id"]))
        timeline.append(
            {
                "session_id": session["agent_session_id"],
                "workflow_node": session["workflow_node"],
                "agent_name": session["agent_name"],
                "round_index": session["round_index"],
                "attempt_index": session["attempt_index"],
                "status": session["status"],
                "started_at": session["started_at"],
                "ended_at": session["ended_at"],
                "request_summaries": _request_summaries(records),
                "response_summaries": _response_summaries(records),
                "tool_call_summaries": _tool_call_summaries(records),
                "final_result": _prune(session["final_result_json"]),
                "error_message": session["error_message"],
            }
        )
    return timeline


def _build_section_evidence_summaries(
    evidence_memory: EvidenceMemory,
    task_id: str,
    section_ids: list[str],
) -> list[dict[str, Any]]:
    summaries = []
    for section_id in section_ids:
        try:
            record = evidence_memory.get_section_evidence(task_id, section_id)
        except KeyError as exc:
            summaries.append({"section_id": section_id, "unavailable_reason": str(exc)})
            continue
        summaries.append({
            "section_id": record.section_id.value,
            "section_items": record.section_items,
            "material_refs": record.material_refs,
            "material_summaries": [item.content_summary for item in record.descriptions],
            "user_notes": [item.note for item in record.materials if item.note],
            "supplement_links": [item.model_dump(mode="json") for item in record.supplement_links],
        })
    return summaries


def _collect_relevant_section_ids(
    final_state: TaskState,
    report_diffs: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> list[str]:
    section_ids = [
        item.section_id.value
        for result in _iter_review_results(final_state, timeline)
        for item in result.issues
        if item.section_id is not None
    ]
    section_ids.extend(change["section_id"] for diff in report_diffs for change in diff["section_changes"])
    return _ordered_unique(section_ids) or [section.value for section in ReportSection]


def _iter_review_results(final_state: TaskState, timeline: list[dict[str, Any]]):
    if final_state.latest_review_result is not None:
        yield final_state.latest_review_result
    for event in timeline:
        final_result = event.get("final_result")
        if not isinstance(final_result, dict):
            continue
        payload = final_result.get("review_result", final_result)
        if isinstance(payload, dict) and payload.get("decision"):
            yield ReviewResult.model_validate(payload)






def _request_summaries(records: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for record in records:
        if record.get("record_type") != "request":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        summaries.extend(_extract_input_texts(payload.get("input")))
    return [_preview_text(item) for item in summaries[:3]]


def _response_summaries(records: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for record in records:
        if record.get("record_type") != "response":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        text = payload.get("output_text")
        if isinstance(text, str) and text.strip():
            summaries.append(text)
    return [_preview_text(item) for item in summaries[:3]]


def _tool_call_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        if record.get("record_type") != "tool_result":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        result = payload.get("result", {})
        tool_call = payload.get("tool_call", {})
        items.append(
            {
                "tool_name": payload.get("tool_name"),
                "arguments": _prune(tool_call.get("arguments") if isinstance(tool_call, dict) else None),
                "status": result.get("status") if isinstance(result, dict) else None,
                "summary": _preview_text(str(result.get("summary", ""))) if isinstance(result, dict) else None,
                "error_message": result.get("error_message") if isinstance(result, dict) else None,
            }
        )
    return items[:5]


def _extract_input_texts(items: Any) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"input_text", "output_text"}:
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
    return texts


def _preview_text(text: str, max_chars: int = 240) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= max_chars else compact[:max_chars] + "...[truncated]"


def _prune(value: Any, max_chars: int = 240) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _preview_text(value, max_chars)
    if isinstance(value, list):
        return [_prune(item, max_chars) for item in value[:5]]
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, item in list(value.items())[:10]:
            if key in {"input", "response_content", "image_url", "image_base64"}:
                continue
            pruned[str(key)] = _prune(item, max_chars)
        return pruned
    return _preview_text(str(value), max_chars)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
