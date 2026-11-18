from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage, message_to_dict
from sqlalchemy import func, select

from app.db.models import AgentSessionIndex, TaskIndex
from app.db.session import create_sqlite_session_factory

class AgentSessionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class AgentTrimPolicy:
    max_text_chars: int = 4000
    keep_last_images: int = 2
    max_history_items: int = 30


@dataclass
class AgentSessionRuntime:
    memory_tool: "MemoryTool"
    agent_session_id: str
    task_id: str
    workflow_node: str
    round_index: int
    context_items: list[dict[str, Any]] = field(default_factory=list)
    keyed_contexts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    next_request_round: int = 1
    active_round: int = 1


class MemoryTool:
    def __init__(self, artifact_root: Path, sqlite_path: Path) -> None:
        self.artifact_root = artifact_root.resolve()
        self.sqlite_path = sqlite_path.resolve()
        self.session_factory = create_sqlite_session_factory(self.sqlite_path)
        self._record_buffers: dict[str, list[dict[str, Any]]] = {}

    def start_agent_runtime(
        self,
        *,
        task_id: str,
        workflow_node: str,
        agent_name: str,
        round_index: int,
    ) -> AgentSessionRuntime:
        agent_session_id = f"asess_{uuid4().hex[:16]}"
        now = self._iso_now()
        transcript_relative_path = (
            Path(task_id) / "derived" / "agent_sessions" / f"{agent_session_id}.jsonl"
        ).as_posix()
        transcript_path = self.artifact_root / transcript_relative_path
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.touch(exist_ok=True)
        with self.session_factory() as session:
            self._require_task(session, task_id)
            attempt_index = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentSessionIndex)
                    .where(
                        AgentSessionIndex.task_id == task_id,
                        AgentSessionIndex.workflow_node == workflow_node,
                        AgentSessionIndex.round_index == round_index,
                    )
                )
                or 0
            ) + 1
            session.add(
                AgentSessionIndex(
                    agent_session_id=agent_session_id,
                    task_id=task_id,
                    workflow_node=workflow_node,
                    agent_name=agent_name,
                    round_index=round_index,
                    attempt_index=attempt_index,
                    status=AgentSessionStatus.RUNNING.value,
                    transcript_relative_path=transcript_relative_path,
                    latest_event_seq=0,
                    final_result_json=None,
                    error_message=None,
                    started_at=now,
                    ended_at=None,
                    updated_at=now,
                )
            )
            session.commit()
        self._record_buffers[agent_session_id] = []
        return AgentSessionRuntime(
            memory_tool=self,
            agent_session_id=agent_session_id,
            task_id=task_id,
            workflow_node=workflow_node,
            round_index=round_index,
            next_request_round=1,
            active_round=1,
        )

    def append_request(
        self,
        runtime: AgentSessionRuntime,
        request_kwargs: dict[str, Any],
        *,
        conversation_key: str | None = None,
    ) -> int:
        runtime.active_round = runtime.next_request_round
        runtime.next_request_round += 1
        return self._append_record(
            runtime,
            record_type="request",
            payload=self._json_safe(request_kwargs),
            conversation_key=conversation_key,
        )

    def append_response(
        self,
        runtime: AgentSessionRuntime,
        response: object,
        *,
        conversation_key: str | None = None,
    ) -> int:
        payload = self.serialize_response(response)
        seq = self._append_record(
            runtime,
            record_type="response",
            payload=payload,
            conversation_key=conversation_key,
        )
        self._context_for(runtime, conversation_key).extend(self._response_to_context_items(payload))
        return seq

    def append_tool_result(
        self,
        runtime: AgentSessionRuntime,
        tool_name: str,
        tool_call: Any,
        result: Any,
        *,
        conversation_key: str | None = None,
    ) -> int:
        payload = {
            "tool_name": tool_name,
            "tool_call": {
                "call_id": self._tool_call_value(tool_call, "call_id", "id"),
                "name": self._tool_call_value(tool_call, "name") or tool_name,
                "arguments": self._json_safe(
                    self._tool_call_value(tool_call, "arguments", "args") or {}
                ),
            },
            "result": self._json_safe(result),
        }
        seq = self._append_record(
            runtime,
            record_type="tool_result",
            payload=payload,
            conversation_key=conversation_key,
        )
        self._context_for(runtime, conversation_key).extend(
            self._tool_result_to_context_items(payload)
        )
        return seq

    def append_messages(
        self,
        runtime: AgentSessionRuntime,
        messages: list[BaseMessage],
    ) -> list[int]:
        return [
            self._append_record(
                runtime,
                record_type="message",
                payload={"message": message_to_dict(message)},
                conversation_key=None,
            )
            for message in messages
        ]

    @staticmethod
    def _tool_call_value(tool_call: Any, *keys: str) -> str:
        if isinstance(tool_call, dict):
            for key in keys:
                value = tool_call.get(key)
                if value is not None:
                    return str(value)
            return ""
        for key in keys:
            value = getattr(tool_call, key, None)
            if value is not None:
                return str(value)
        return ""

    def replay_runtime(
        self,
        agent_session_id: str,
        base_context: list[dict[str, Any]],
        *,
        conversation_key: str | None = None,
        policy: AgentTrimPolicy | None = None,
    ) -> AgentSessionRuntime:
        session_info = self.get_agent_session(agent_session_id)
        runtime = AgentSessionRuntime(
            memory_tool=self,
            agent_session_id=agent_session_id,
            task_id=str(session_info["task_id"]),
            workflow_node=str(session_info["workflow_node"]),
            round_index=int(session_info["round_index"]),
        )
        self._record_buffers.setdefault(agent_session_id, self.load_records(agent_session_id))
        context = self._context_for(runtime, conversation_key)
        context.extend(copy.deepcopy(base_context))
        max_round = 0
        for record in self.load_records(agent_session_id):
            round_index = int(record.get("round_index") or 0)
            max_round = max(max_round, round_index)
            record_key = record.get("conversation_key")
            if record_key != conversation_key:
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or record.get("record_type") == "request":
                continue
            if record["record_type"] == "response":
                context.extend(self._response_to_context_items(payload))
            if record["record_type"] == "tool_result":
                context.extend(self._tool_result_to_context_items(payload))
        runtime.next_request_round = max(max_round + 1, 1)
        runtime.active_round = max(max_round, 1)
        self.compact_context(runtime, conversation_key=conversation_key, policy=policy)
        return runtime

    def ensure_context(
        self,
        runtime: AgentSessionRuntime,
        base_context: list[dict[str, Any]],
        *,
        conversation_key: str | None = None,
        policy: AgentTrimPolicy | None = None,
    ) -> list[dict[str, Any]]:
        context = self._context_for(runtime, conversation_key)
        if context:
            return context
        restored = self.replay_runtime(
            runtime.agent_session_id,
            base_context,
            conversation_key=conversation_key,
            policy=policy,
        )
        if conversation_key is None:
            runtime.context_items = restored.context_items
        else:
            runtime.keyed_contexts[conversation_key] = restored.keyed_contexts.get(conversation_key, [])
        runtime.next_request_round = max(runtime.next_request_round, restored.next_request_round)
        runtime.active_round = max(runtime.active_round, restored.active_round)
        return self._context_for(runtime, conversation_key)

    def compact_context(
        self,
        runtime: AgentSessionRuntime,
        *,
        conversation_key: str | None = None,
        policy: AgentTrimPolicy | None = None,
    ) -> list[dict[str, Any]]:
        trim_policy = policy or AgentTrimPolicy()
        context = self._context_for(runtime, conversation_key)
        trimmed = [copy.deepcopy(item) for item in context[-trim_policy.max_history_items :]]
        self._replace_old_images(trimmed, trim_policy)
        self._truncate_text(trimmed, trim_policy)
        context.clear()
        context.extend(trimmed)
        return context

    def serialize_response(self, response: object) -> dict[str, Any]:
        for method_name, kwargs in (("model_dump", {"mode": "json"}), ("model_dump", {}), ("dict", {})):
            method = getattr(response, method_name, None)
            if callable(method):
                try:
                    payload = method(**kwargs)
                except TypeError:
                    continue
                if isinstance(payload, dict):
                    return self._json_safe(payload)
        return self._json_safe(
            {
                "id": getattr(response, "id", None),
                "status": getattr(response, "status", None),
                "output_text": getattr(response, "output_text", None),
                "output": getattr(response, "output", None),
                "usage": getattr(response, "usage", None),
                "error": getattr(response, "error", None),
            }
        )

    def complete_agent_session(self, runtime: AgentSessionRuntime | str, final_result: dict[str, Any]) -> None:
        self._finish_agent_session(
            runtime if isinstance(runtime, str) else runtime.agent_session_id,
            status=AgentSessionStatus.COMPLETED,
            final_result=final_result,
            error_message=None,
        )

    def fail_agent_session(self, runtime: AgentSessionRuntime | str, error: Exception | str) -> None:
        self._finish_agent_session(
            runtime if isinstance(runtime, str) else runtime.agent_session_id,
            status=AgentSessionStatus.FAILED,
            final_result=None,
            error_message=str(error),
        )

    def get_agent_session(self, agent_session_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = self._require_agent_session(session, agent_session_id)
            return self._row_to_dict(row)

    def list_agent_sessions(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentSessionIndex)
                .where(AgentSessionIndex.task_id == task_id)
                .order_by(AgentSessionIndex.started_at.asc())
            ).all()
        return [self._row_to_dict(row) for row in rows]

    def load_records(self, agent_session_id: str) -> list[dict[str, Any]]:
        cached = self._record_buffers.get(agent_session_id)
        if cached is not None:
            return copy.deepcopy(cached)
        session_info = self.get_agent_session(agent_session_id)
        path = self.artifact_root / session_info["transcript_relative_path"]
        records: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if isinstance(payload, dict):
                    records.append(payload)
        self._record_buffers[agent_session_id] = copy.deepcopy(records)
        return records

    def _append_record(
        self,
        runtime: AgentSessionRuntime,
        *,
        record_type: str,
        payload: dict[str, Any],
        conversation_key: str | None,
    ) -> int:
        with self.session_factory() as session:
            row = self._require_agent_session(session, runtime.agent_session_id)
            seq = row.latest_event_seq + 1
            record = {
                "seq": seq,
                "timestamp": self._iso_now(),
                "record_type": record_type,
                "agent_session_id": runtime.agent_session_id,
                "round_index": runtime.active_round,
                "conversation_key": conversation_key,
                "payload": payload,
            }
            path = self.artifact_root / row.transcript_relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            row.latest_event_seq = seq
            row.updated_at = record["timestamp"]
            session.commit()
        self._record_buffers.setdefault(runtime.agent_session_id, []).append(record)
        return seq

    def _finish_agent_session(
        self,
        agent_session_id: str,
        *,
        status: AgentSessionStatus,
        final_result: dict[str, Any] | None,
        error_message: str | None,
    ) -> None:
        now = self._iso_now()
        with self.session_factory() as session:
            row = self._require_agent_session(session, agent_session_id)
            row.status = status.value
            row.ended_at = now
            row.updated_at = now
            row.error_message = error_message
            row.final_result_json = (
                json.dumps(final_result, ensure_ascii=False, sort_keys=True)
                if final_result is not None
                else None
            )
            session.commit()

    def _context_for(
        self,
        runtime: AgentSessionRuntime,
        conversation_key: str | None,
    ) -> list[dict[str, Any]]:
        if conversation_key is None:
            return runtime.context_items
        return runtime.keyed_contexts.setdefault(conversation_key, [])

    def _response_to_context_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        output = payload.get("output")
        if isinstance(output, list):
            for output_item in output:
                if not isinstance(output_item, dict):
                    continue
                if output_item.get("type") == "message":
                    role = str(output_item.get("role") or "assistant")
                    content = output_item.get("content")
                    if isinstance(content, list):
                        items.append({"role": role, "content": self._json_safe(content)})
        if items:
            return items
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return [{"role": "assistant", "content": [{"type": "output_text", "text": output_text}]}]
        return []

    def _tool_result_to_context_items(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        items: list[dict[str, Any]] = []
        tool_call = payload.get("tool_call")
        tool_call_id = ""
        if isinstance(tool_call, dict):
            tool_call_id = str(tool_call.get("call_id") or "")
        compact_output = self._compact_text(
            json.dumps(
                {
                    "tool_name": payload.get("tool_name"),
                    "result": result,
                },
                ensure_ascii=False,
            )
        )
        if tool_call_id:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": compact_output,
                }
            )
        response_content = result.get("response_content")
        if isinstance(response_content, list) and response_content:
            items.append({"role": "user", "content": self._json_safe(response_content)})
            return items
        followup_text = result.get("followup_message")
        if isinstance(followup_text, str) and followup_text.strip():
            items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": followup_text.strip()}],
                }
            )
            return items
        items.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": compact_output,
                    }
                ],
            }
        )
        return items

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, dict):
            return {str(key): MemoryTool._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [MemoryTool._json_safe(item) for item in value]
        if is_dataclass(value):
            return MemoryTool._json_safe(value.__dict__)
        for method_name in ("model_dump", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return MemoryTool._json_safe(method())
                except TypeError:
                    continue
        if hasattr(value, "__dict__"):
            return MemoryTool._json_safe(vars(value))
        return str(value)

    @staticmethod
    def _compact_text(text: str, max_chars: int = 4000) -> str:
        return text if len(text) <= max_chars else text[:max_chars] + "...[truncated]"

    def _replace_old_images(
        self,
        items: list[dict[str, Any]],
        policy: AgentTrimPolicy,
    ) -> None:
        image_locations: list[tuple[int, int]] = []
        for item_index, item in enumerate(items):
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_index, content_item in enumerate(content):
                if isinstance(content_item, dict) and content_item.get("type") == "input_image":
                    image_locations.append((item_index, content_index))
        keep = set(image_locations[-policy.keep_last_images :])
        for item_index, content_index in image_locations:
            if (item_index, content_index) in keep:
                continue
            content = items[item_index]["content"]
            image_item = content[content_index]
            image_ref = ""
            if isinstance(image_item, dict):
                image_ref = str(image_item.get("artifact_ref") or image_item.get("image_ref") or "")
            content[content_index] = {
                "type": "input_text",
                "text": f"[Older image omitted from context. artifact_ref={image_ref or '(unknown)'}]",
            }

    def _truncate_text(
        self,
        items: list[dict[str, Any]],
        policy: AgentTrimPolicy,
    ) -> None:
        full_items_start = max(len(items) - 4, 0)
        for index, item in enumerate(items):
            if index >= full_items_start:
                continue
            if item.get("type") == "function_call_output":
                output = item.get("output")
                if isinstance(output, str):
                    item["output"] = self._compact_text(output, policy.max_text_chars)
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    content_item["text"] = self._compact_text(text, policy.max_text_chars)

    @staticmethod
    def _row_to_dict(row: AgentSessionIndex) -> dict[str, Any]:
        return {
            "agent_session_id": row.agent_session_id,
            "task_id": row.task_id,
            "workflow_node": row.workflow_node,
            "agent_name": row.agent_name,
            "round_index": row.round_index,
            "attempt_index": row.attempt_index,
            "status": row.status,
            "transcript_relative_path": row.transcript_relative_path,
            "latest_event_seq": row.latest_event_seq,
            "final_result_json": (
                json.loads(row.final_result_json)
                if row.final_result_json
                else None
            ),
            "error_message": row.error_message,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _require_task(session: Any, task_id: str) -> TaskIndex:
        task = session.get(TaskIndex, task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task

    @staticmethod
    def _require_agent_session(session: Any, agent_session_id: str) -> AgentSessionIndex:
        row = session.get(AgentSessionIndex, agent_session_id)
        if row is None:
            raise KeyError(f"Unknown agent session id: {agent_session_id}")
        return row

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()
