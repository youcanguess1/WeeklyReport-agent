from __future__ import annotations

from datetime import UTC, datetime

from langgraph.types import Command

from app.schemas.review import WorkflowDecision
from app.schemas.task import TaskStatus
from app.workflows.graph_nodes import MockDecisionProvider, WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason
from app.workflows.weekly_report_graph import build_weekly_report_graph


def _state(**overrides: object) -> TaskState:
    payload = {
        "task_id": "task-001",
        "thread_id": "thread-001",
        "run_id": "run-001",
    }
    payload.update(overrides)
    return TaskState(**payload)


def _run(
    state: TaskState,
    decisions: list[WorkflowDecision],
    nodes: WeeklyReportGraphNodes | None = None,
    confirm_approved: bool = False,
) -> TaskState:
    graph_nodes = nodes or WeeklyReportGraphNodes(review_decision_provider=MockDecisionProvider(decisions))
    graph = build_weekly_report_graph(nodes=graph_nodes)
    result = TaskState.model_validate(graph.invoke(state))
    while confirm_approved and result.waiting_reason == WorkflowWaitReason.HUMAN and result.latest_review_result is not None and result.latest_review_result.decision == WorkflowDecision.APPROVED:
        result = TaskState.model_validate(
            graph.invoke(
                result.model_copy(
                    update={
                        "task_status": TaskStatus.QUEUED,
                        "waiting_reason": None,
                        "last_completed_node": WorkflowNode.WAIT_FOR_HUMAN,
                    }
                )
            )
        )
    return result


def test_graph_requires_confirmation_before_completing_report() -> None:
    waiting = _run(_state(), [WorkflowDecision.APPROVED])

    assert waiting.task_status == TaskStatus.WAITING
    assert waiting.waiting_reason == WorkflowWaitReason.HUMAN
    assert waiting.last_completed_node == WorkflowNode.WAIT_FOR_HUMAN

    final_state = _run(_state(), [WorkflowDecision.APPROVED], confirm_approved=True)

    assert final_state.task_status == TaskStatus.COMPLETED
    assert final_state.last_completed_node == WorkflowNode.MARK_COMPLETED
    assert final_state.writer_round == 1
    assert final_state.review_round == 1
    assert final_state.current_report_version_ref is not None
    assert final_state.latest_review_result is not None


def test_text_report_skips_material_analysis() -> None:
    class TextReportNodes(WeeklyReportGraphNodes):
        def material_understanding(self, state: TaskState) -> Command:
            raise AssertionError("Text reports must route directly to the writer.")

    final_state = _run(
        _state(),
        [WorkflowDecision.APPROVED],
        nodes=TextReportNodes(),
        confirm_approved=True,
    )

    assert final_state.task_status == TaskStatus.COMPLETED
    assert final_state.writer_round == 1
    assert final_state.review_round == 1


def test_new_image_report_routes_through_material_analysis() -> None:
    from app.schemas.evidence import ArtifactKind, ArtifactRef

    state = _state(

        input_artifact_refs=[
            ArtifactRef(
                artifact_id="image-001",
                kind=ArtifactKind.IMAGE_MATERIAL,
                path="/tmp/image.png",
                media_type="image/png",
                created_at=datetime.now(UTC),
            )
        ],
    )
    nodes = WeeklyReportGraphNodes()

    assert nodes._start_target_for(state) == WorkflowNode.MATERIAL_UNDERSTANDING.value


def test_review_revise_loops_to_writer_until_max_review_rounds() -> None:
    final_state = _run(
        _state(max_review_rounds=2),
        [WorkflowDecision.REVISE_REPORT, WorkflowDecision.REVISE_REPORT],
    )

    assert final_state.task_status == TaskStatus.WAITING
    assert final_state.waiting_reason == WorkflowWaitReason.HUMAN
    assert final_state.last_completed_node == WorkflowNode.WAIT_FOR_HUMAN
    assert final_state.writer_round == 2
    assert final_state.review_round == 2


def test_max_review_rounds_can_be_accepted_without_another_review_cycle() -> None:
    graph_nodes = WeeklyReportGraphNodes(

        review_decision_provider=MockDecisionProvider(
            [WorkflowDecision.REVISE_REPORT, WorkflowDecision.REVISE_REPORT]
        ),
    )
    graph = build_weekly_report_graph(nodes=graph_nodes)
    waiting = TaskState.model_validate(
        graph.invoke(_state(max_review_rounds=2))
    )

    accepted = TaskState.model_validate(
        graph.invoke(
            waiting.model_copy(
                update={
                    "task_status": TaskStatus.QUEUED,
                    "waiting_reason": None,
                    "last_completed_node": WorkflowNode.WAIT_FOR_HUMAN,
                }
            )
        )
    )

    assert accepted.task_status == TaskStatus.COMPLETED
    assert accepted.last_completed_node == WorkflowNode.MARK_COMPLETED
    assert accepted.review_round == 2


def test_request_evidence_enters_stable_wait_state() -> None:
    final_state = _run(_state(), [WorkflowDecision.REQUEST_EVIDENCE])

    assert final_state.task_status == TaskStatus.WAITING
    assert final_state.waiting_reason == WorkflowWaitReason.EVIDENCE
    assert final_state.last_completed_node == WorkflowNode.WAIT_FOR_EVIDENCE


def test_human_review_decision_enters_stable_wait_state() -> None:
    final_state = _run(_state(), [WorkflowDecision.HUMAN_REVIEW_REQUIRED])

    assert final_state.task_status == TaskStatus.WAITING
    assert final_state.waiting_reason == WorkflowWaitReason.HUMAN
    assert final_state.last_completed_node == WorkflowNode.WAIT_FOR_HUMAN


def test_fail_decision_marks_workflow_failed() -> None:
    final_state = _run(_state(), [WorkflowDecision.FAIL])

    assert final_state.task_status == TaskStatus.FAILED
    assert final_state.last_completed_node == WorkflowNode.MARK_FAILED


def test_cancel_requested_before_workflow_marks_canceled() -> None:
    final_state = _run(_state(cancel_requested=True), [WorkflowDecision.APPROVED])

    assert final_state.task_status == TaskStatus.CANCELED
    assert final_state.last_completed_node == WorkflowNode.MARK_CANCELED
    assert final_state.writer_round == 0


def test_cancel_requested_during_workflow_marks_canceled() -> None:
    class CancelAfterWriterNodes(WeeklyReportGraphNodes):
        def write_report(self, state: TaskState):
            command = super().write_report(state)
            command.update["cancel_requested"] = True
            return command

    final_state = _run(
        _state(),
        [WorkflowDecision.APPROVED],
        nodes=CancelAfterWriterNodes(review_decision_provider=MockDecisionProvider([WorkflowDecision.APPROVED])),
    )

    assert final_state.task_status == TaskStatus.CANCELED
    assert final_state.cancel_requested is True
    assert final_state.last_completed_node == WorkflowNode.MARK_CANCELED
    assert final_state.review_round == 0
