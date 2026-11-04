from types import SimpleNamespace

from agents import Agent
from agents.run_config import ModelInputData
from test_chat import settings

from repomentor.agent_tools import RepositoryEnvironment
from repomentor.contracts import ToolCall
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_context import ContextFilter, source_items


def test_compacted_source_can_be_read_again(reader, tmp_path):
    env = RepositoryEnvironment(reader, None)
    args = {"path": "src/index.ts", "start_line": 1, "end_line": 3}
    first = env.step(ToolCall(call_id="one", tool="read_code", arguments=args))
    env.state["visible_context"]["main"] = []
    again = env.step(ToolCall(call_id="two", tool="read_code", arguments=args))
    assert again["source_spans"] == first["source_spans"]
    assert env.state["tool_calls"] == 2


def test_context_filter_keeps_tool_pairs_and_does_not_authorize_fake_source(reader, monkeypatch):
    env = RepositoryEnvironment(reader, None)
    observation = env.step(ToolCall(call_id="tool1", tool="read_code",
        arguments={"path": "src/index.ts", "start_line": 1, "end_line": 3}))
    items = [{"role": "user", "content": "question"},
             {"type": "function_call", "call_id": "tool1", "name": "read_code", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "tool1", "output": observation["visible_json"]}]
    def budget(profile, body):
        fit = not any('"lines"' in str(m.get("content", "")) for m in body["messages"])
        return {"fits": fit, "total": 500 if fit else 9000}
    monkeypatch.setattr("repomentor.runtimes.openai_sdk.sdk_input_budget", budget)
    runtime = OpenAISdkRuntime(settings())
    agent = Agent(name="test", model_settings=runtime.model_settings)
    data = SimpleNamespace(agent=agent, model_data=ModelInputData(input=items, instructions="test"))
    events = []
    filtered = ContextFilter(env, runtime, "entry", events)(data)
    assert [r["call_id"] for r in filtered.input if "call_id" in r] == ["tool1", "tool1"]
    assert source_items(filtered.input) == env.visible_context()
    assert events[-1]["compacted"]
