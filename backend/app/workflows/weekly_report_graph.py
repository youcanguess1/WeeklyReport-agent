from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowState, WorkflowNode


def build_weekly_report_graph(
    nodes: WeeklyReportGraphNodes | None = None,
    checkpointer=None,
):
    graph_nodes = nodes or WeeklyReportGraphNodes()

    graph = StateGraph(WorkflowState)
    graph.add_node(WorkflowNode.START_WORKFLOW.value, _typed_node(graph_nodes.start_workflow))
    graph.add_node(WorkflowNode.MATERIAL_UNDERSTANDING.value, _typed_node(graph_nodes.material_understanding))
    graph.add_node(WorkflowNode.WRITE_REPORT.value, _typed_node(graph_nodes.write_report))
    graph.add_node(WorkflowNode.REVIEW_REPORT.value, _typed_node(graph_nodes.review_report))
    graph.add_node(WorkflowNode.WAIT_FOR_EVIDENCE.value, _typed_node(graph_nodes.wait_for_evidence))
    graph.add_node(WorkflowNode.WAIT_FOR_HUMAN.value, _typed_node(graph_nodes.wait_for_human))
    graph.add_node(WorkflowNode.MARK_COMPLETED.value, _typed_node(graph_nodes.mark_completed))
    graph.add_node(WorkflowNode.MARK_FAILED.value, _typed_node(graph_nodes.mark_failed))
    graph.add_node(WorkflowNode.MARK_CANCELED.value, _typed_node(graph_nodes.mark_canceled))

    graph.add_edge(START, WorkflowNode.START_WORKFLOW.value)
    graph.add_edge(WorkflowNode.WAIT_FOR_EVIDENCE.value, END)
    graph.add_edge(WorkflowNode.WAIT_FOR_HUMAN.value, END)
    graph.add_edge(WorkflowNode.MARK_COMPLETED.value, END)
    graph.add_edge(WorkflowNode.MARK_FAILED.value, END)
    graph.add_edge(WorkflowNode.MARK_CANCELED.value, END)

    return graph.compile(checkpointer=checkpointer)


def _typed_node(node):
    def invoke(state):
        typed_state = state if isinstance(state, TaskState) else TaskState.model_validate(state)
        return node(typed_state)

    return invoke
