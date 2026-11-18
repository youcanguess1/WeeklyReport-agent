from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason

__all__ = [
    "MockDecisionProvider",
    "WeeklyReportGraphNodes",
    "TaskState",
    "WorkflowNode",
    "WorkflowWaitReason",
    "build_weekly_report_graph",
]


def __getattr__(name: str):
    if name in {"MockDecisionProvider", "WeeklyReportGraphNodes"}:
        from app.workflows.graph_nodes import MockDecisionProvider, WeeklyReportGraphNodes

        exports = {
            "MockDecisionProvider": MockDecisionProvider,
            "WeeklyReportGraphNodes": WeeklyReportGraphNodes,
        }
        return exports[name]
    if name == "build_weekly_report_graph":
        from app.workflows.weekly_report_graph import build_weekly_report_graph

        return build_weekly_report_graph
    raise AttributeError(name)
