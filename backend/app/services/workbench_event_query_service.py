from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, messages_from_dict

from app.schemas.trace import TraceLogEntry
from app.schemas.workbench import WorkbenchEvent
from app.services.artifact_service import ArtifactService
from app.services.trace_service import TraceService


VISIBLE_WORKFLOW_NODES = {
    "start_workflow": "周报准备",
    "material_understanding": "图片分析 / 材料理解",
    "write_report": "周报 Writer",
    "review_report": "周报 Review",
    "mark_completed": "周报输出",
    "human_feedback": "用户反馈",
}

TOOL_LABELS = {
    "get_skill_content": "读取技能内容",
}

START_MESSAGES = {
    "start_workflow": "正在准备周报材料",
    "material_understanding": "正在分析图片材料",
    "write_report": "正在生成项目周报",
    "review_report": "正在审核项目周报",
    "wait_for_evidence": "等待用户提供补充证据",
    "wait_for_human": "等待用户提交处理意见",
    "mark_completed": "正在完成任务",
    "mark_failed": "正在处理失败状态",
    "mark_canceled": "正在取消任务",
}


class WorkbenchEventQuery:
    def __init__(self, artifact_service: ArtifactService, trace_service: TraceService) -> None:
        self.artifact_service = artifact_service
        self.trace_service = trace_service

    def list_events(self, task_id: str, cursor: str | None = None) -> tuple[list[WorkbenchEvent], str]:
        decoded = _decode_cursor(cursor)
        trace_logs = self._trace_logs(task_id)
        sessions = self.artifact_service.memory_tool.list_agent_sessions(task_id)
        events = self._node_events(task_id, trace_logs, sessions, decoded["trace"])
        events.extend(self._session_events(sessions, decoded["sessions"]))
        events.sort(key=lambda item: (item.timestamp, item.event_id))
        next_trace = _trace_cursor(trace_logs[-1]) if trace_logs else decoded["trace"]
        next_sessions = {
            str(session["agent_session_id"]): int(session.get("latest_event_seq") or 0)
            for session in sessions
        }
        return events, _encode_cursor({"trace": next_trace, "sessions": next_sessions})

    def _trace_logs(self, task_id: str) -> list[TraceLogEntry]:
        try:
            trace = self.trace_service.get_trace_by_task_id(task_id)
        except KeyError:
            return []
        return self.trace_service.list_logs(trace.trace_id)

    def _node_events(
        self,
        task_id: str,
        logs: list[TraceLogEntry],
        sessions: list[dict[str, Any]],
        after: dict[str, str] | None,
    ) -> list[WorkbenchEvent]:
        events: list[WorkbenchEvent] = []
        resumed_trace_ids = {
            log.trace_id
            for log in logs
            if log.event_type == "workflow_started"
            and log.extra.get("last_completed_node")
        }
        for index, log in enumerate(logs, start=1):
            if after and _event_position(log.timestamp, log.log_id) <= (
                after.get("timestamp", ""),
                after.get("log_id", ""),
            ):
                continue
            if log.event_type not in {"node_started", "node_completed", "node_failed"}:
                continue
            if log.stage == "start_workflow" and log.trace_id in resumed_trace_ids:
                continue
            agent = VISIBLE_WORKFLOW_NODES.get(log.stage)
            if agent is None:
                continue
            events.append(
                WorkbenchEvent(
                    event_id=log.log_id,
                    event_type=log.event_type,
                    timestamp=log.timestamp,
                    agent=agent,
                    role="assistant",
                    kind="action",
                    content=self._node_content(task_id, log, sessions),
                    action_label=agent,
                    session_id=log.trace_id,
                    sequence=index,
                    request_id=None,
                )
            )
        return events

    def _session_events(
        self,
        sessions: list[dict[str, Any]],
        cursors: dict[str, int],
    ) -> list[WorkbenchEvent]:
        events: list[WorkbenchEvent] = []
        for session in sessions:
            session_id = str(session["agent_session_id"])
            workflow_node = str(session["workflow_node"])
            agent = VISIBLE_WORKFLOW_NODES.get(workflow_node)
            if agent is None:
                continue
            after_seq = int(cursors.get(session_id, 0))
            for record in self.artifact_service.memory_tool.load_records(session_id):
                sequence = int(record.get("seq") or 0)
                if sequence <= after_seq:
                    continue
                timestamp = _as_datetime(record.get("timestamp"))
                payload = record.get("payload")
                if record.get("record_type") == "tool_result" and isinstance(payload, dict):
                    tool_name = str(payload.get("tool_name") or "")
                    action_label = TOOL_LABELS.get(tool_name, "执行工作流工具")
                    events.append(
                        WorkbenchEvent(
                            event_id=f"{session_id}:{sequence}",
                            event_type="action",
                            timestamp=timestamp,
                            agent=agent,
                            role="assistant",
                            kind="action",
                            content="已完成",
                            action_label=action_label,
                            session_id=session_id,
                            sequence=sequence,
                            request_id=None,
                        )
                    )
                elif record.get("record_type") == "message" and workflow_node == "human_feedback":
                    events.extend(self._human_message_events(session_id, agent, sequence, timestamp, payload))
            if workflow_node == "human_feedback":
                review_result = session.get("final_result_json")
                if isinstance(review_result, dict) and isinstance(review_result.get("review_result"), dict):
                    sequence = int(session.get("latest_event_seq") or 0)
                    if sequence > after_seq:
                        events.append(
                            WorkbenchEvent(
                                event_id=f"{session_id}:review_result",
                                event_type="node_completed",
                                timestamp=_as_datetime(session.get("ended_at")),
                                agent="周报 Review",
                                role="assistant",
                                kind="action",
                                content=_review_content(review_result, include_evidence_details=True),
                                action_label="周报 Review",
                                session_id=session_id,
                                sequence=sequence,
                                request_id=None,
                            )
                        )
        return events

    @staticmethod
    def _human_message_events(
        session_id: str,
        agent: str,
        sequence: int,
        timestamp: datetime,
        payload: Any,
    ) -> list[WorkbenchEvent]:
        message_payload = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message_payload, dict):
            return []
        try:
            message = messages_from_dict([message_payload])[0]
        except (TypeError, ValueError, KeyError):
            return []
        if not isinstance(message, HumanMessage):
            return []
        content = _human_content(message.content)
        if not content:
            return []
        return [
            WorkbenchEvent(
                event_id=f"{session_id}:{sequence}",
                event_type="message",
                timestamp=timestamp,
                agent=agent,
                role="user",
                kind="message",
                content=content,
                action_label=None,
                session_id=session_id,
                sequence=sequence,
                request_id=_request_id_from_message(message),
            )
        ]

    def _node_content(
        self,
        task_id: str,
        log: TraceLogEntry,
        sessions: list[dict[str, Any]],
    ) -> str:
        stage = log.stage
        if log.event_type == "node_started":
            if stage == "wait_for_evidence":
                return _evidence_waiting_content(sessions, log.timestamp)
            return START_MESSAGES.get(stage, "正在处理工作流节点")
        if log.event_type == "node_failed":
            return f"处理失败：{_readable_error(log.message)}"
        result = _session_result_for_node(sessions, stage, log.timestamp)
        if stage == "material_understanding":
            count = result.get("understanding_count")
            return f"材料分析完成，识别出 {count} 个内容阶段" if count is not None else "材料分析完成"
        if stage == "write_report":
            version = result.get("version")
            prefix = "已根据审核意见更新项目周报" if _is_revision_result(result) else "已生成项目周报初稿"
            return f"{prefix}（{version}）" if version else prefix
        if stage == "review_report":
            return _review_content(
                result,
                include_evidence_details=True,
                review_limit_reached=self._review_limit_reached(task_id, result),
            )
        if stage == "mark_completed":
            return self._final_report_content(task_id)
        if stage == "wait_for_evidence":
            return _evidence_waiting_content(sessions, log.timestamp)
        if stage == "wait_for_human":
            return "等待用户提交处理意见"
        if stage == "mark_canceled":
            return "任务已取消"
        if stage == "mark_failed":
            return "任务处理失败"
        if stage == "start_workflow":
            return "周报材料已准备完成"
        return "节点处理完成"

    def _final_report_content(self, task_id: str) -> str:
        metadata = self.artifact_service.get_task_metadata(task_id)
        final_artifact_id = metadata.get("final_report_artifact_id")
        if final_artifact_id:
            try:
                artifact = self.artifact_service.get_artifact_ref(str(final_artifact_id))
                return f"项目周报已生成\n保存位置：{artifact.path}"
            except KeyError:
                pass
        versions = self.artifact_service.list_report_versions(task_id)
        if not versions:
            return "项目周报已生成"
        artifact = self.artifact_service.get_artifact_ref(str(versions[-1]["markdown_artifact_id"]))
        return f"项目周报已生成\n保存位置：{artifact.path}"

    def _review_limit_reached(self, task_id: str, result: dict[str, Any]) -> bool:
        review = result.get("review_result") if isinstance(result.get("review_result"), dict) else result
        if str(review.get("decision") or "").lower() != "revise_report":
            return False
        try:
            state = self.artifact_service.load_workflow_state(task_id)
        except KeyError:
            return False
        if not isinstance(state, dict):
            return False
        return int(state.get("review_round") or 0) >= int(state.get("max_review_rounds") or 0) > 0


def _session_result_for_node(
    sessions: list[dict[str, Any]], workflow_node: str, timestamp: datetime
) -> dict[str, Any]:
    candidates = [
        session
        for session in sessions
        if str(session.get("workflow_node")) == workflow_node
        and isinstance(session.get("final_result_json"), dict)
    ]
    if not candidates:
        return {}
    ended = [session for session in candidates if session.get("ended_at")]
    if ended:
        before = [session for session in ended if _as_datetime(session["ended_at"]) <= timestamp]
        if before:
            selected = before[-1]
        else:
            selected = candidates[-1]
    else:
        selected = candidates[-1]
    result = dict(selected["final_result_json"])
    result["_session_round_index"] = selected.get("round_index")
    result["_session_change_note"] = selected.get("change_note")
    return result


def _is_revision_result(result: dict[str, Any]) -> bool:
    return int(result.get("_session_round_index") or 1) > 1 or str(result.get("_session_change_note") or "").lower().startswith("revision")


def _review_content(
    result: dict[str, Any],
    *,
    include_evidence_details: bool = True,
    review_limit_reached: bool = False,
) -> str:
    review = result.get("review_result") if isinstance(result.get("review_result"), dict) else result
    decision = str(review.get("decision") or "").lower()
    if decision == "approved":
        return "审核通过\n是否选择当前周报作为最终版"
    if decision == "revise_report":
        if review_limit_reached:
            return "已达到自动审核轮次上限，当前周报仍有未解决问题。\n是否选择当前周报作为最终版？"
        return "审核发现需要修改，已交给周报 Writer 修订"
    if decision == "request_evidence":
        if not include_evidence_details:
            return "审核发现需要补充证据，已进入补充证据等待"
        lines = ["审核发现需要补充证据"]
        for issue in review.get("issues") or []:
            if isinstance(issue, dict) and issue.get("requires_evidence"):
                description = str(issue.get("description") or "").strip()
                if description:
                    lines.append(f"问题：{description[:240]}")
        for request in review.get("evidence_requests") or []:
            if not isinstance(request, dict):
                continue
            reason = str(request.get("reason") or "").strip()
            requested = request.get("requested_inputs") or []
            if reason:
                lines.append(f"需要证据：{reason[:240]}")
            if requested:
                lines.append(f"证据类型：{', '.join(str(item) for item in requested[:5])}")
        return "\n".join(lines)
    if decision == "human_review_required":
        return "审核完成，等待人工处理"
    if decision == "fail":
        return "审核未通过，任务将结束"
    return "审核完成"


def _evidence_waiting_content(sessions: list[dict[str, Any]], timestamp: datetime) -> str:
    review_result = _session_result_for_node(sessions, "review_report", timestamp)
    review = (
        review_result.get("review_result")
        if isinstance(review_result.get("review_result"), dict)
        else review_result
    )
    if str(review.get("decision") or "").lower() == "request_evidence":
        return _review_content(review_result)
    return "等待用户提供补充证据"


def _readable_error(message: str) -> str:
    """Keep the first human-readable error sentence out of Trace internals."""
    value = str(message or "").strip()
    if ":" in value:
        value = value.split(":", 1)[1].strip()
    value = value.splitlines()[0].strip()
    value = re.sub(
        r"\b(?:task|run|trace|call|artifact|operation|op|asess|review|report_v|thread|log)[_-][A-Za-z0-9-]+\b",
        "内部标识",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b[0-9a-f]{32}\b",
        "内部标识",
        value,
        flags=re.IGNORECASE,
    )
    if not value or value.startswith(("{", "[")):
        return "节点执行失败"
    return value[:240]


def _human_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    text_parts: list[str] = []
    has_image = False
    for item in content:
        if isinstance(item, str) and item.strip():
            text_parts.append(item.strip())
        elif isinstance(item, dict):
            if item.get("type") in {"image_url", "input_image"}:
                has_image = True
            value = item.get("text")
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
    if has_image:
        text_parts.append("[已附加图片]")
    return "\n".join(text_parts) or None


def _request_id_from_message(message: Any) -> str | None:
    additional_kwargs = getattr(message, "additional_kwargs", {})
    value = additional_kwargs.get("request_id") if isinstance(additional_kwargs, dict) else None
    return str(value) if value else None


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _event_position(timestamp: datetime, event_id: str) -> tuple[str, str]:
    return timestamp.isoformat(), event_id


def _trace_cursor(log: TraceLogEntry) -> dict[str, str]:
    return {"timestamp": log.timestamp.isoformat(), "log_id": log.log_id}


def _encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> dict[str, Any]:
    if not value:
        return {"trace": None, "sessions": {}}
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("cursor must be an object")
        trace = decoded.get("trace")
        sessions = decoded.get("sessions")
        if trace is not None and not isinstance(trace, dict):
            raise ValueError("trace cursor must be an object")
        if not isinstance(sessions, dict):
            raise ValueError("session cursor must be an object")
        return {"trace": trace, "sessions": {str(key): int(item) for key, item in sessions.items()}}
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid workbench cursor.") from exc
