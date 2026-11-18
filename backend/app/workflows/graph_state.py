from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.schemas.evidence import ArtifactRef
from app.schemas.review import ReviewResult, WorkflowDecision
from app.schemas.task import TaskStatus


class WorkflowNode(StrEnum):
    START_WORKFLOW = "start_workflow"
    MATERIAL_UNDERSTANDING = "material_understanding"
    WRITE_REPORT = "write_report"
    REVIEW_REPORT = "review_report"
    WAIT_FOR_EVIDENCE = "wait_for_evidence"
    WAIT_FOR_HUMAN = "wait_for_human"
    MARK_COMPLETED = "mark_completed"
    MARK_FAILED = "mark_failed"
    MARK_CANCELED = "mark_canceled"


class WorkflowWaitReason(StrEnum):
    EVIDENCE = "evidence"
    HUMAN = "human"
    MANUAL_PAUSE = "manual_pause"


class WorkflowState(TypedDict, total=False):
    """LangGraph state contract.

    Business payloads remain Pydantic models at node boundaries, while each
    Agent owns a separate reducer-backed message history.
    """

    task_id: str
    thread_id: str
    run_id: str
    task_status: TaskStatus
    waiting_reason: WorkflowWaitReason | None
    last_completed_node: WorkflowNode | None
    workflow_decision: WorkflowDecision | None
    review_round: int
    supplement_round: int
    writer_round: int
    max_review_rounds: int
    input_artifact_refs: list[ArtifactRef]
    material_pack_ref: ArtifactRef | None
    supplement_artifact_refs: list[ArtifactRef]
    current_report_version_ref: ArtifactRef | None
    report_version_history: list[ArtifactRef]
    latest_review_result: ReviewResult | None
    revision_instructions: str | None
    resolved_issue_ids: list[str]
    cancel_requested: bool
    pause_requested: bool
    updated_at: datetime

    material_understanding_messages: Annotated[list[BaseMessage], add_messages]
    report_writer_messages: Annotated[list[BaseMessage], add_messages]
    report_review_messages: Annotated[list[BaseMessage], add_messages]
    memory_evolution_messages: Annotated[list[BaseMessage], add_messages]


class TaskState(BaseModel):
    task_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)

    task_status: TaskStatus = TaskStatus.CREATED
    waiting_reason: WorkflowWaitReason | None = None
    last_completed_node: WorkflowNode | None = None

    workflow_decision: WorkflowDecision | None = None

    review_round: int = Field(default=0, ge=0)
    supplement_round: int = Field(default=0, ge=0)
    writer_round: int = Field(default=0, ge=0)
    max_review_rounds: int = Field(default=3, ge=1)

    input_artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    material_pack_ref: ArtifactRef | None = None
    supplement_artifact_refs: list[ArtifactRef] = Field(default_factory=list)


    current_report_version_ref: ArtifactRef | None = None
    report_version_history: list[ArtifactRef] = Field(default_factory=list)

    latest_review_result: ReviewResult | None = None
    revision_instructions: str | None = None
    resolved_issue_ids: list[str] = Field(default_factory=list)

    cancel_requested: bool = False
    pause_requested: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # LangChain messages are kept on the snapshot boundary for API projection
    # and audit. WorkflowState uses the stricter BaseMessage annotations.
    material_understanding_messages: list[Any] = Field(default_factory=list)
    report_writer_messages: list[Any] = Field(default_factory=list)
    report_review_messages: list[Any] = Field(default_factory=list)
    memory_evolution_messages: list[Any] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC)
