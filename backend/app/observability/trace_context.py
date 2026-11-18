from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class TraceContext:
    task_id: str | None = None
    trace_id: str | None = None
    workflow_node: str | None = None
    agent_session_id: str | None = None


_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "weekly_report_trace_context",
    default=None,
)


def get_current_trace_context() -> TraceContext | None:
    return _TRACE_CONTEXT.get()


def set_trace_context(context: TraceContext | None) -> Token[TraceContext | None]:
    return _TRACE_CONTEXT.set(context)


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    _TRACE_CONTEXT.reset(token)


def clear_trace_context() -> None:
    _TRACE_CONTEXT.set(None)


@contextmanager
def push_trace_context(
    *,
    task_id: str | None = None,
    trace_id: str | None = None,
    workflow_node: str | None = None,
    agent_session_id: str | None = None,
) -> Iterator[TraceContext]:
    current = get_current_trace_context() or TraceContext()
    next_context = TraceContext(
        task_id=task_id if task_id is not None else current.task_id,
        trace_id=trace_id if trace_id is not None else current.trace_id,
        workflow_node=workflow_node if workflow_node is not None else current.workflow_node,
        agent_session_id=(
            agent_session_id if agent_session_id is not None else current.agent_session_id
        ),
    )
    token = set_trace_context(next_context)
    try:
        yield next_context
    finally:
        reset_trace_context(token)
