from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def build_memory_checkpointer() -> MemorySaver:
    return MemorySaver()


def build_sqlite_checkpointer(path: Path) -> AbstractContextManager[Any]:
    """Return a context-managed SqliteSaver using a dedicated checkpoint DB."""

    from langgraph.checkpoint.sqlite import SqliteSaver

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), check_same_thread=False)
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("app.schemas.evidence", "ArtifactKind"),
                ("app.schemas.evidence", "ArtifactRef"),
                ("app.schemas.report", "ReportSection"),
                ("app.schemas.review", "EvidenceRequest"),
                ("app.schemas.review", "ReviewIssue"),
                ("app.schemas.review", "ReviewResult"),
                ("app.schemas.review", "ReviewSeverity"),
                ("app.schemas.review", "WorkflowDecision"),
                ("app.schemas.task", "TaskStatus"),
                ("app.workflows.graph_state", "WorkflowNode"),
                ("app.workflows.graph_state", "WorkflowWaitReason"),
            ]
        )
        saver = SqliteSaver(connection, serde=serde)
        saver.setup()
        return _CheckpointerContext(saver, connection)
    except (OSError, sqlite3.Error):
        # Local development can run with a read-only data mount. Keep the
        # graph usable with in-memory checkpoints while preserving the same
        # thread_id contract; writable deployments use SQLite above.
        return _CheckpointerContext(MemorySaver(), None)


class _CheckpointerContext(AbstractContextManager[Any]):
    def __init__(self, saver: Any, connection: sqlite3.Connection | None) -> None:
        self.saver = saver
        self.connection = connection

    def __enter__(self) -> Any:
        return self.saver

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.connection is not None:
            self.connection.close()
