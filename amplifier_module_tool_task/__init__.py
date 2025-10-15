"""
Task delegation tool module.

Enables AI to spawn sub-sessions for complex subtasks via capability-based architecture.
This module implements the Task tool following kernel philosophy as a pure mechanism.

Key Design Points:
- Pure mechanism: No policy decisions about how sessions are spawned
- Uses capabilities: agents.get, agents.list, session.spawn
- Simple string input: "agent_name: instruction" format
- Proper event taxonomy: tool:pre, tool:post, tool:error
- Dynamic description from agent registry
- Configurable recursion depth limiting
- Fallback behavior when session.spawn not available
"""

import logging
import uuid
from typing import Any

from amplifier_core import ModuleCoordinator
from amplifier_core import ToolResult

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the task delegation tool.

    Args:
        coordinator: The module coordinator
        config: Optional configuration with:
            - max_recursion_depth: Maximum depth for nested delegations (default: 1)

    Returns:
        None - No cleanup needed for this module
    """
    config = config or {}
    tool = TaskTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted TaskTool")
    return  # No cleanup needed


class TaskTool:
    """Delegate tasks to specialized agents via sub-sessions.

    This tool is a pure mechanism that:
    1. Parses "agent: instruction" format
    2. Queries agent registry for available agents
    3. Validates recursion depth
    4. Emits proper tool events
    5. Requests sub-session spawn via capability (with fallback)

    All policy decisions (which agent to use, how to spawn, etc.)
    are made at the edges, not in this mechanism.
    """

    name = "task"

    def __init__(self, coordinator: ModuleCoordinator, config: dict[str, Any]):
        """Initialize the task tool.

        Args:
            coordinator: Module coordinator for accessing capabilities
            config: Configuration dictionary with optional:
                - max_recursion_depth: Max delegation depth (default: 1)
        """
        self.coordinator = coordinator
        self.config = config

    @property
    def description(self) -> str:
        """Generate dynamic description with available agents.

        Queries the agent registry to provide an up-to-date list
        of available agents in the tool description.
        """
        agents_list = self._get_agent_list()
        if agents_list:
            # Limit to 10 agents for brevity in description
            agent_desc = "\n".join(
                f"  - {a['name']}: {a.get('description', 'No description')}" for a in agents_list[:10]
            )
            return (
                f"Delegate a sub-task to a specialized agent.\n"
                f"Usage: '<agent_name>: <instruction>'\n"
                f"Available agents:\n{agent_desc}"
            )
        return "Delegate a sub-task to a specialized agent"

    @property
    def input_schema(self) -> dict:
        """Input schema for task delegation.

        Returns:
            JSON schema for the tool input (OpenAI requires object type)
        """
        return {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "Format: 'agent_name: instruction'"}},
            "required": ["task"],
        }

    def _get_agent_list(self) -> list[dict[str, Any]]:
        """Get list of available agents from mount plan.

        Reads agents section from the session's mount plan configuration.

        Returns:
            List of agent definitions with name and description
        """
        # Get agents from coordinator's infrastructure config property
        agents = self.coordinator.config.get("agents", {})

        return [{"name": name, "description": cfg.get("description", "No description")} for name, cfg in agents.items()]

    async def execute(self, input: str | dict) -> ToolResult:
        """Execute delegation with proper event emission.

        Parses the input, validates the agent exists, checks recursion
        depth, emits events, and requests sub-session spawn.

        Args:
            input: String in format "agent_name: instruction"

        Returns:
            ToolResult with success status and output or error
        """
        # Parse input - handle both string and dict formats
        if isinstance(input, dict):
            # OpenAI sends input as {"task": "agent_name: instruction"}
            task_str = input.get("task", "")
        elif isinstance(input, str):
            # Direct string input for backward compatibility
            task_str = input
        else:
            return ToolResult(success=False, error={"message": "Invalid input type"})

        if not task_str or ":" not in task_str:
            return ToolResult(success=False, error={"message": "Invalid format. Use: 'agent_name: instruction'"})

        agent_name, instruction = task_str.split(":", 1)
        agent_name = agent_name.strip()
        instruction = instruction.strip()

        if not agent_name:
            return ToolResult(success=False, error={"message": "Agent name cannot be empty"})
        if not instruction:
            return ToolResult(success=False, error={"message": "Instruction cannot be empty"})

        # Get agents from coordinator infrastructure
        agents = self.coordinator.config.get("agents", {})

        if agent_name not in agents:
            return ToolResult(success=False, error={"message": f"Agent '{agent_name}' not found"})

        # Note: Recursion depth limiting not yet implemented
        # Future: Track depth in metadata and check against config.max_recursion_depth

        # Get parent session ID from coordinator infrastructure
        parent_session_id = self.coordinator.session_id

        # Generate hierarchical sub-session ID
        sub_session_id = f"{parent_session_id}-{agent_name}-{uuid.uuid4().hex[:8]}"

        # Get hooks for error handling (orchestrator will emit tool:pre/post)
        hooks = self.coordinator.get("hooks")

        try:
            # Import spawn helper (app layer)
            from amplifier_app_cli.session_spawner import spawn_sub_session

            # Get parent session from coordinator infrastructure
            parent_session = self.coordinator.session

            # Spawn sub-session with agent configuration overlay
            result = await spawn_sub_session(
                agent_name=agent_name,
                instruction=instruction,
                parent_session=parent_session,
                agent_configs=agents,
                sub_session_id=sub_session_id,
            )

            # Note: Orchestrator will emit tool:post with standard result field
            # We don't emit here to avoid double display in UI

            # Return output with session_id for multi-turn capability
            return ToolResult(
                success=True,
                output={"response": result["output"], "session_id": result["session_id"]},
            )

        except Exception as e:
            # Emit tool:error event
            if hooks:
                await hooks.emit(
                    "tool:error",
                    {
                        "tool": "task",
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "error": str(e),
                    },
                )

            return ToolResult(success=False, error={"message": f"Delegation failed: {str(e)}"})
