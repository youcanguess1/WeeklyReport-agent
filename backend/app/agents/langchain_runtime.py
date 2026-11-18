from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing import Annotated

from app.observability import ObservabilityProvider, ObservedToolExecutor


class AgentLoopState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int


class AgentLoopResult:
    def __init__(
        self,
        messages: list[BaseMessage],
        structured: Any = None,
        new_messages: list[BaseMessage] | None = None,
    ) -> None:
        self.messages = messages
        self.structured = structured
        self.new_messages = list(new_messages or [])


def run_tool_loop(
    *,
    model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool | Any] | None = None,
    max_tool_rounds: int = 4,
    output_schema: Any = None,
    terminal_tool_names: set[str] | None = None,
    observability_provider: ObservabilityProvider | None = None,
    observation_name: str = "agent",
) -> AgentLoopResult:
    """Run an isolated official LangGraph tool loop for one Agent.

    The loop always owns a private ``messages`` key. The caller decides which
    Agent-specific State key is used for the returned messages.
    """

    tools = list(tools or [])
    observed_tools = _instrument_tools(tools, observability_provider)
    bound_model = model.bind_tools(observed_tools) if observed_tools else model

    def call_model(state: AgentLoopState) -> dict[str, Any]:
        rounds = int(state.get("tool_rounds", 0))
        response = _invoke_with_observation(
            bound_model,
            state["messages"],
            observability_provider=observability_provider,
            name=f"generation.{observation_name}.tool_round_{rounds + 1}",
        )
        if isinstance(response, AIMessage) and response.tool_calls:
            if rounds >= max_tool_rounds:
                raise RuntimeError("Agent exceeded the maximum number of tool rounds.")
            return {"messages": [response], "tool_rounds": rounds + 1}
        return {"messages": [response]}

    builder = StateGraph(AgentLoopState)
    builder.add_node("agent", call_model)
    if observed_tools:
        builder.add_node("tools", ToolNode(observed_tools, messages_key="messages"))
        builder.add_conditional_edges(
            "agent",
            lambda state: tools_condition(state, messages_key="messages"),
            {"tools": "tools", "__end__": END},
        )
        def route_after_tools(state: AgentLoopState) -> str:
            if terminal_tool_names:
                last_message = state.get("messages", [])[-1] if state.get("messages") else None
                if isinstance(last_message, ToolMessage) and last_message.name in terminal_tool_names:
                    return "__end__"
            return "agent"

        builder.add_conditional_edges(
            "tools",
            route_after_tools,
            {"agent": "agent", "__end__": END},
        )
    else:
        builder.add_edge("agent", END)
    builder.set_entry_point("agent")
    loop = builder.compile()
    result = loop.invoke(
        {"messages": messages, "tool_rounds": 0},
        config={"recursion_limit": max(8, max_tool_rounds * 3 + 4)},
    )
    result_messages = list(result.get("messages", []))
    new_messages = result_messages[len(messages) :]
    structured = None
    if output_schema is not None:
        structured = _invoke_with_observation(
            model.with_structured_output(output_schema),
            result_messages,
            observability_provider=observability_provider,
            name=f"generation.{observation_name}.structured_output",
        )
    return AgentLoopResult(result_messages, structured, new_messages)


def _instrument_tools(
    tools: list[BaseTool | Any],
    provider: ObservabilityProvider | None,
) -> list[BaseTool | Any]:
    if provider is None or not callable(
        getattr(provider.get_sdk_client(), "start_as_current_observation", None)
    ):
        return tools

    executor = ObservedToolExecutor(provider)
    observed: list[BaseTool | Any] = []
    for tool in tools:
        if not isinstance(tool, BaseTool):
            observed.append(tool)
            continue

        def make_invoke(source_tool: BaseTool):
            def invoke_tool(**tool_kwargs: Any) -> Any:
                return executor.execute(
                    source_tool.name,
                    lambda: source_tool.invoke(tool_kwargs),
                    raw_input=tool_kwargs,
                )

            return invoke_tool

        observed.append(
            StructuredTool.from_function(
                func=make_invoke(tool),
                name=tool.name,
                description=tool.description,
                args_schema=tool.args_schema,
            )
        )
    return observed


def _invoke_with_observation(
    runnable: Any,
    messages: list[BaseMessage],
    *,
    observability_provider: ObservabilityProvider | None,
    name: str,
) -> Any:
    if observability_provider is None:
        return runnable.invoke(messages)

    input_summary = {
        "message_count": len(messages),
        "messages": [_message_summary(message) for message in messages[-8:]],
    }
    with observability_provider.observation(
        name=name,
        as_type="generation",
        input=input_summary,
        metadata={"message_count": len(messages)},
    ) as observation:
        result = runnable.invoke(messages)
        if observation is not None:
            observation.update(
                output=_message_summary(result),
                model=_model_name(runnable),
                usage_details=_usage_details(result),
            )
        return result


def _message_summary(message: Any) -> dict[str, Any]:
    if isinstance(message, BaseMessage):
        content = message.content
        content_length = len(content) if isinstance(content, str) else len(str(content))
        summary: dict[str, Any] = {
            "message_type": message.__class__.__name__,
            "role": getattr(message, "type", None),
            "content_length": content_length,
        }
        if isinstance(message, ToolMessage):
            summary["tool_name"] = message.name
            summary["tool_call_id"] = message.tool_call_id
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            summary["tool_calls"] = [
                {
                    "name": call.get("name"),
                    "id": call.get("id"),
                    "argument_keys": sorted((call.get("args") or {}).keys()),
                }
                for call in tool_calls
            ]
        return summary
    if hasattr(message, "model_dump"):
        return {"result_type": type(message).__name__, "fields": sorted(message.model_dump(mode="json"))}
    return {"result_type": type(message).__name__}


def _usage_details(message: Any) -> dict[str, int] | None:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return None
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            result[key] = value
    return result or None


def _model_name(runnable: Any) -> str | None:
    for key in ("model_name", "model", "model_id"):
        value = getattr(runnable, key, None)
        if isinstance(value, str) and value:
            return value
    return None
