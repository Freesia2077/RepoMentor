import asyncio
from pathlib import Path

import claude_agent_sdk as sdk
import jsonschema
import pytest

from repomentor.config import ModelSettings
from repomentor.contracts import ContributorOutput, EvidencePlan, ExplorerOutput, MentorOutput
from repomentor.harness import RepositoryHarness
from repomentor.runtimes.claude import ClaudeSdkRuntime, RuntimeFailure, draft7_schema
from repomentor.skills import load_skill


def terminal(**kwargs):
    return sdk.ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="fake-session",
        **kwargs,
    )


def valid_plan():
    return {
        "goal": "inspect",
        "rationale": "test",
        "questions": [],
        "actions": [],
        "files": [{"path": "src/index.ts", "purpose": "entry", "priority": "high"}],
        "stopConditions": ["done"],
    }


class FakeClient:
    messages = []
    created = []
    delay = 0

    def __init__(self, options):
        self.options = options
        self.closed = self.interrupted = False
        type(self).created.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def query(self, prompt):
        self.prompt = prompt

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        await asyncio.sleep(self.delay)
        batch = type(self).messages.pop(0)
        for message in batch:
            yield message


@pytest.fixture
def setup(reader):
    FakeClient.created = []
    FakeClient.messages = []
    FakeClient.delay = 0
    h = RepositoryHarness(reader)
    h.get_repository_map()
    h.activate_policy("explorer", load_skill([], "explorer")[0])
    settings = ModelSettings(
        apiKey="secret", baseUrl="https://example.invalid", model="test", timeout_seconds=1
    )
    return h, ClaudeSdkRuntime(settings, client_factory=FakeClient)


@pytest.mark.parametrize("model", [EvidencePlan, ExplorerOutput, MentorOutput, ContributorOutput])
def test_schema_draft07(model):
    schema = draft7_schema(model)
    jsonschema.Draft7Validator.check_schema(schema)
    assert "$defs" not in schema


async def test_restricted_options_hook_and_cleanup(setup):
    h, runtime = setup
    FakeClient.messages = [[terminal(structured_output=valid_plan())]]
    output = await runtime.plan("explorer", h, "trusted skill")
    client = FakeClient.created[0]
    options = client.options
    assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config
    assert options.permission_mode == "dontAsk"
    assert options.extra_args == {"no-session-persistence": None}
    assert "Bash" in options.disallowed_tools and "Agent" in options.disallowed_tools
    assert not Path(options.cwd).exists()
    assert client.closed
    assert output.actions == []
    hook = options.hooks["PreToolUse"][0].hooks[0]
    for tool, expected in [
        ("Bash", "deny"),
        ("Read", "deny"),
        ("mcp__foreign__read", "deny"),
        ("StructuredOutput", "allow"),
        ("mcp__repomentor__search_symbols", "allow"),
    ]:
        result = await hook({"tool_name": tool}, None, {})
        assert result["hookSpecificOutput"]["permissionDecision"] == expected
    denied_child = await hook({"tool_name": "StructuredOutput", "agent_id": "child"}, None, {})
    assert denied_child["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_schema_repair_has_no_tools(setup):
    h, runtime = setup
    FakeClient.messages = [
        [terminal(structured_output={"goal": "incomplete"})],
        [terminal(structured_output=valid_plan())],
    ]
    await runtime.plan("explorer", h, "")
    assert len(FakeClient.created) == 2
    assert FakeClient.created[1].options.mcp_servers == {}
    assert all(c.closed for c in FakeClient.created)
    assert h.stages["explorer"].discoveryActionsUsed == 0


async def test_no_fallback_on_missing_structured_output(setup):
    h, runtime = setup
    FakeClient.messages = [[terminal(result="plain text response")]]
    with pytest.raises(RuntimeFailure) as exc:
        await runtime.plan("explorer", h, "")
    assert exc.value.code == "structured_output_unsupported"
    assert len(FakeClient.created) == 1
    assert FakeClient.created[0].closed


async def test_timeout_is_not_capability_failure(setup):
    h, runtime = setup
    FakeClient.delay = 0.2
    runtime.settings.timeout_seconds = 0.02
    with pytest.raises(RuntimeFailure) as exc:
        await runtime.plan("explorer", h, "")
    assert exc.value.code == "timeout"
    assert len(FakeClient.created) == 1 and FakeClient.created[0].closed


async def test_cancel_interrupts_and_closes(setup):
    h, runtime = setup
    FakeClient.delay = 0.15
    FakeClient.messages = [[terminal(structured_output=valid_plan())]]
    asyncio.get_running_loop().call_later(0.03, h.token.cancel)
    with pytest.raises(Exception) as exc:
        await runtime.plan("explorer", h, "")
    assert exc.value.code == "cancelled"
    assert FakeClient.created[0].interrupted and FakeClient.created[0].closed


@pytest.mark.parametrize(
    "status,code", [(401, "authentication"), (429, "rate_limit"), (503, "provider_unavailable")]
)
async def test_provider_error_categories_do_not_retry(setup, status, code):
    h, runtime = setup
    message = terminal(api_error_status=status)
    message.is_error = True
    message.subtype = "error_during_execution"
    FakeClient.messages = [[message]]
    with pytest.raises(RuntimeFailure) as exc:
        await runtime.plan("explorer", h, "")
    assert exc.value.code == code
    assert len(FakeClient.created) == 1 and FakeClient.created[0].closed


async def test_mcp_handler_enforces_harness_even_without_hook(setup, monkeypatch):
    h, runtime = setup
    original = sdk.create_sdk_mcp_server
    registered = []

    def capture(**kwargs):
        registered.extend(kwargs["tools"])
        return original(**kwargs)

    monkeypatch.setattr(sdk, "create_sdk_mcp_server", capture)
    runtime.make_options("unused-test-path", "explorer", EvidencePlan, "system", h, True)
    # Invoke the actual SDK-registered handler directly, bypassing SDK permission hooks.
    handler = next(t.handler for t in registered if t.name == "find_related_tests")
    result = await handler({"paths": ["../secret"], "purpose": "escape"})
    assert result["isError"]
    assert h.stages["explorer"].discoveryActionsUsed == 0
