"""
Tests for delegation timeout and cancellation in TaskTool.

Covers:
- Default and custom timeout configuration
- asyncio.wait_for wrapping of spawn_fn and resume_fn
- Per-call timeout override via input["timeout"]
- ToolResult structure on timeout (DELEGATION_TIMEOUT code + session_id)
- task:agent_timeout event emission
- Successful calls that finish within the timeout
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_module_tool_task import TaskTool


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.session_id = "parent-session-123"
    coord.config = {"agents": {"test-agent": {"model": "test"}}}

    # hooks mock — emit is async
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    coord.get.return_value = hooks

    # session mock
    session = MagicMock()
    session.config = {}
    coord.session = session

    return coord


@pytest.fixture
def tool(mock_coordinator):
    """TaskTool with a 2-second timeout for fast tests."""
    return TaskTool(mock_coordinator, {"timeout": 2})


@pytest.fixture
def default_tool(mock_coordinator):
    """TaskTool with default (300 s) timeout."""
    return TaskTool(mock_coordinator, {})


# ──────────────────────────────────────────────────────────────────────────────
# 1. Configuration
# ──────────────────────────────────────────────────────────────────────────────

def test_default_timeout_is_300_seconds(default_tool):
    assert default_tool.timeout == 300.0


def test_custom_timeout_from_config(mock_coordinator):
    t = TaskTool(mock_coordinator, {"timeout": 42})
    assert t.timeout == 42.0


def test_timeout_stored_as_float(mock_coordinator):
    t = TaskTool(mock_coordinator, {"timeout": "60"})
    assert t.timeout == 60.0
    assert isinstance(t.timeout, float)


def test_default_max_recursion_depth(default_tool):
    assert default_tool.max_recursion_depth == 5


def test_custom_max_recursion_depth(mock_coordinator):
    t = TaskTool(mock_coordinator, {"max_recursion_depth": 3})
    assert t.max_recursion_depth == 3


# ──────────────────────────────────────────────────────────────────────────────
# 2. Spawn timeout
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spawn_timeout_returns_error_with_session_id(tool, mock_coordinator):
    """spawn_fn that hangs forever → DELEGATION_TIMEOUT ToolResult."""

    async def hanging_spawn(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.return_value = hanging_spawn

    result = await tool.execute({
        "agent": "test-agent",
        "instruction": "do something",
    })

    assert result.success is False
    assert result.error["code"] == "DELEGATION_TIMEOUT"
    assert "test-agent" in result.error["message"]
    assert "session_id" in result.error
    # session_id must be non-empty
    assert result.error["session_id"]


@pytest.mark.asyncio
async def test_timeout_error_includes_delegation_timeout_code(tool, mock_coordinator):
    async def hanging_spawn(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.return_value = hanging_spawn

    result = await tool.execute({
        "agent": "test-agent",
        "instruction": "work",
    })

    assert result.error["code"] == "DELEGATION_TIMEOUT"


@pytest.mark.asyncio
async def test_per_call_timeout_overrides_config(mock_coordinator):
    """input["timeout"] = 1 s overrides the 300-s config default."""
    tool = TaskTool(mock_coordinator, {"timeout": 300})

    async def hanging_spawn(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.return_value = hanging_spawn

    # Should time out in ~1 s, not 300 s
    result = await tool.execute({
        "agent": "test-agent",
        "instruction": "work",
        "timeout": 1,
    })

    assert result.success is False
    assert result.error["code"] == "DELEGATION_TIMEOUT"


@pytest.mark.asyncio
async def test_successful_spawn_within_timeout(tool, mock_coordinator):
    """spawn_fn that finishes quickly → success."""
    fake_sub_session_id = "sub-abc-123"

    async def fast_spawn(**kwargs):
        return {"output": "all done", "session_id": fake_sub_session_id}

    mock_coordinator.get_capability.return_value = fast_spawn

    result = await tool.execute({
        "agent": "test-agent",
        "instruction": "quick task",
    })

    assert result.success is True
    assert result.output["response"] == "all done"


@pytest.mark.asyncio
async def test_timeout_emits_task_agent_timeout_event(tool, mock_coordinator):
    """A spawn timeout must emit task:agent_timeout."""
    async def hanging_spawn(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.return_value = hanging_spawn

    hooks = mock_coordinator.get.return_value

    await tool.execute({
        "agent": "test-agent",
        "instruction": "work",
    })

    # Verify the event was emitted
    emitted_events = [call.args[0] for call in hooks.emit.call_args_list]
    assert "task:agent_timeout" in emitted_events


@pytest.mark.asyncio
async def test_spawn_timeout_event_payload(tool, mock_coordinator):
    """task:agent_timeout payload contains agent, timeout, and parent_session_id."""
    async def hanging_spawn(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.return_value = hanging_spawn
    hooks = mock_coordinator.get.return_value

    await tool.execute({
        "agent": "test-agent",
        "instruction": "work",
    })

    timeout_calls = [
        call for call in hooks.emit.call_args_list
        if call.args[0] == "task:agent_timeout"
    ]
    assert timeout_calls, "task:agent_timeout was not emitted"
    payload = timeout_calls[0].args[1]
    assert payload["agent"] == "test-agent"
    assert payload["timeout"] == tool.timeout
    assert payload["parent_session_id"] == "parent-session-123"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Resume timeout
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_timeout_returns_error(tool, mock_coordinator):
    """resume_fn that hangs forever → DELEGATION_TIMEOUT ToolResult."""

    async def hanging_resume(**kwargs):
        await asyncio.sleep(9999)

    # get_capability: first call (session.spawn) returns None so we fall through
    # to resume path; resume capability is returned on second call.
    mock_coordinator.get_capability.side_effect = lambda cap: (
        hanging_resume if cap == "session.resume" else None
    )

    result = await tool.execute({
        "session_id": "existing-session-abc",
        "instruction": "continue",
    })

    assert result.success is False
    assert result.error["code"] == "DELEGATION_TIMEOUT"
    assert result.error["session_id"] == "existing-session-abc"
    assert "existing-session-abc" in result.error["message"]


@pytest.mark.asyncio
async def test_resume_timeout_emits_event(tool, mock_coordinator):
    async def hanging_resume(**kwargs):
        await asyncio.sleep(9999)

    mock_coordinator.get_capability.side_effect = lambda cap: (
        hanging_resume if cap == "session.resume" else None
    )
    hooks = mock_coordinator.get.return_value

    await tool.execute({
        "session_id": "existing-session-abc",
        "instruction": "continue",
    })

    emitted_events = [call.args[0] for call in hooks.emit.call_args_list]
    assert "task:agent_timeout" in emitted_events


@pytest.mark.asyncio
async def test_successful_resume_within_timeout(tool, mock_coordinator):
    """resume_fn that finishes quickly → success."""

    async def fast_resume(**kwargs):
        return {"output": "resumed ok", "session_id": "existing-session-abc"}

    mock_coordinator.get_capability.side_effect = lambda cap: (
        fast_resume if cap == "session.resume" else None
    )

    result = await tool.execute({
        "session_id": "existing-session-abc",
        "instruction": "continue",
    })

    assert result.success is True
    assert result.output["response"] == "resumed ok"
