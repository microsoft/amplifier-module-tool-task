"""
Tests for the redesigned task delegation tool.
"""

from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest
from amplifier_core import ModuleCoordinator

pytestmark = pytest.mark.asyncio


@pytest.fixture
def coordinator():
    """Create a mock coordinator with basic setup."""
    coord = ModuleCoordinator()
    # Set up hooks as a mock
    mock_hooks = AsyncMock()
    mock_hooks.emit = AsyncMock()
    coord.mount_points["hooks"] = mock_hooks
    coord.hooks = mock_hooks
    return coord


@pytest.fixture
def mock_agents():
    """Create mock agent definitions."""
    return {
        "researcher": {
            "name": "researcher",
            "description": "Research and analysis agent",
            "config": {"temperature": 0.7},
        },
        "coder": {"name": "coder", "description": "Code implementation agent", "config": {"temperature": 0.3}},
    }


@pytest.fixture
def task_tool(coordinator, mock_agents):
    """Create a task tool instance with mock agents."""
    from amplifier_module_tool_task import TaskTool

    # Register agent capabilities (new capability-based approach)
    def list_agents():
        return [
            {
                "name": "researcher",
                "description": "Research and analysis agent",
                "model": "anthropic/claude",
                "tools": [],
            },
            {"name": "coder", "description": "Code implementation agent", "model": "anthropic/claude", "tools": []},
        ]

    def get_agent(name):
        return mock_agents.get(name)

    coordinator.register_capability("agents.list", list_agents)
    coordinator.register_capability("agents.get", get_agent)

    config = {"max_recursion_depth": 2}
    return TaskTool(coordinator, config)


class TestTaskTool:
    """Test suite for TaskTool."""

    async def test_tool_properties(self, task_tool):
        """Test basic tool properties."""
        assert task_tool.name == "task"
        assert "<agent_name>: <instruction>" in task_tool.description
        assert task_tool.input_schema["type"] == "object"

    async def test_description_with_agents(self, task_tool):
        """Test dynamic description includes available agents."""
        desc = task_tool.description
        assert "researcher" in desc
        assert "coder" in desc
        assert "Available agents:" in desc

    async def test_description_without_agents(self, coordinator):
        """Test description when no agents available."""
        from amplifier_module_tool_task import TaskTool

        tool = TaskTool(coordinator, {})
        desc = tool.description
        assert "Delegate a sub-task to a specialized agent" in desc

    async def test_input_parsing_valid(self, task_tool):
        """Test parsing valid input format."""
        result = await task_tool.execute("researcher: Find information about Python")
        assert result.success is True
        assert "researcher" in result.output
        assert "Python" in result.output

    async def test_input_parsing_invalid_format(self, task_tool):
        """Test error on invalid input format."""
        result = await task_tool.execute("no colon here")
        assert result.success is False
        assert "Invalid format" in result.error["message"]

    async def test_input_parsing_dict(self, task_tool):
        """Test parsing dict input (OpenAI format)."""
        result = await task_tool.execute({"task": "researcher: Find info about Python"})
        assert result.success is True
        assert "researcher" in result.output
        assert "Python" in result.output

    async def test_empty_agent_name(self, task_tool):
        """Test error on empty agent name."""
        result = await task_tool.execute(": some instruction")
        assert result.success is False
        assert "Agent name cannot be empty" in result.error["message"]

    async def test_empty_instruction(self, task_tool):
        """Test error on empty instruction."""
        result = await task_tool.execute("researcher: ")
        assert result.success is False
        assert "Instruction cannot be empty" in result.error["message"]

    async def test_agent_not_found(self, task_tool):
        """Test error when agent doesn't exist."""
        result = await task_tool.execute("unknown: Do something")
        assert result.success is False
        assert "Agent 'unknown' not found" in result.error["message"]

    async def test_recursion_depth_check(self, task_tool):
        """Test recursion depth limiting."""
        # Set up context with existing depth
        context = Mock()
        context.metadata = {"task_depth": 2}
        task_tool.coordinator.mount_points["context"] = context

        result = await task_tool.execute("researcher: Deep task")
        assert result.success is False
        assert "Maximum recursion depth" in result.error["message"]

    async def test_event_emission_pre(self, task_tool, coordinator):
        """Test tool:pre event emission."""
        await task_tool.execute("researcher: Test task")

        # Check tool:pre event was emitted
        from unittest.mock import ANY

        coordinator.hooks.emit.assert_any_call(
            "tool:pre",
            {
                "tool": "task",
                "agent": "researcher",
                "instruction": "Test task",
                "sub_session_id": ANY,
                "parent_session_id": None,
                "depth": 1,
            },
        )

    async def test_event_emission_post(self, task_tool, coordinator):
        """Test tool:post event emission."""
        await task_tool.execute("researcher: Test task")

        # Check tool:post event was emitted
        from unittest.mock import ANY

        coordinator.hooks.emit.assert_any_call(
            "tool:post",
            {
                "tool": "task",
                "agent": "researcher",
                "sub_session_id": ANY,
                "parent_session_id": None,
                "status": "ok",
            },
        )

    async def test_parent_session_id_from_context(self, task_tool, coordinator):
        """Test getting parent session ID from context."""
        # Set up context with session_id in metadata
        context = Mock()
        context.metadata = {"task_depth": 0, "session_id": "parent-123"}
        task_tool.coordinator.mount_points["context"] = context

        await task_tool.execute("researcher: Test")

        # Check events include parent_session_id
        from unittest.mock import ANY

        coordinator.hooks.emit.assert_any_call(
            "tool:pre",
            {
                "tool": "task",
                "agent": "researcher",
                "instruction": "Test",
                "sub_session_id": ANY,
                "parent_session_id": "parent-123",
                "depth": 1,
            },
        )

    async def test_fallback_response(self, task_tool):
        """Test fallback response when session.spawn not available."""
        result = await task_tool.execute("researcher: Complex analysis")
        assert result.success is True
        assert "[Would delegate to researcher]" in result.output
        assert "Complex analysis" in result.output

    async def test_error_handling_with_event(self, task_tool):
        """Test error event emission when agent registry not available."""
        # Remove the agents.get capability to simulate registry unavailable
        task_tool.coordinator._capabilities.pop("agents.get", None)

        result = await task_tool.execute("researcher: Test")
        assert result.success is False
        assert "Agent registry not available" in result.error["message"]

    async def test_mount_function(self, coordinator):
        """Test the mount function."""
        from amplifier_module_tool_task import mount

        config = {"max_recursion_depth": 3}
        cleanup = await mount(coordinator, config)

        # Check tool was mounted
        tools = coordinator.get("tools")
        assert "task" in tools
        assert tools["task"].name == "task"
        assert cleanup is None  # No cleanup needed

    async def test_mount_function_no_config(self, coordinator):
        """Test mount function with no config."""
        from amplifier_module_tool_task import mount

        await mount(coordinator, None)

        tools = coordinator.get("tools")
        assert "task" in tools


class TestIntegration:
    """Integration tests with real coordinator."""

    async def test_full_workflow(self):
        """Test complete workflow with real coordinator."""
        from amplifier_module_tool_task import mount

        # Create real coordinator
        coordinator = ModuleCoordinator()

        # Register agent capabilities
        coordinator.register_capability(
            "agents.list",
            lambda: [{"name": "test_agent", "description": "Test agent", "model": "test/model", "tools": []}],
        )
        coordinator.register_capability(
            "agents.get", lambda name: {"name": name, "model": "test/model"} if name == "test_agent" else None
        )

        # Mount task tool
        await mount(coordinator, {"max_recursion_depth": 1})

        # Get the tool
        task_tool = coordinator.get("tools", "task")
        assert task_tool is not None

        # Execute delegation
        result = await task_tool.execute("test_agent: Perform test")
        assert result.success is True
        assert "test_agent" in result.output


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
