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

    # Declare observable lifecycle events for this module
    # (hooks-logging will auto-discover and log these)
    # Get existing list, extend, then re-register (aggregation pattern)
    obs_events = coordinator.get_capability("observability.events") or []
    obs_events.extend(
        [
            "task:agent_spawned",  # When agent sub-session spawned
            "task:agent_resumed",  # When agent sub-session resumed
            "task:agent_completed",  # When agent sub-session completed
        ]
    )
    coordinator.register_capability("observability.events", obs_events)

    tool = TaskTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted TaskTool with observable events")
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
            agent_desc = "\n".join(f"  - {a['name']}: {a.get('description', 'No description')}" for a in agents_list)
            return (
                """
Launch a new agent to handle complex, multi-step tasks autonomously.

The task tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has
specific capabilities and tools available to it.

When using the task tool, you must specify an agent parameter to select which agent type to use.

When NOT to use the task tool:
- If you want to read a specific file path, use the read_file or glob tool instead of the task tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the glob tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the read_file tool instead of the task tool, to
find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:
- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Task tool use content blocks. For example, if you need to launch both a code-reviewer agent and a test-runner agent in parallel, send a single message with both tool calls.
- While each agent invocation is stateless, if you DO need to re-engage with an existing agent session, use the session_id returned from the initial agent response to resume the session instead of creating a new one.

Example usage:

<example_agent_descriptions>
"code-reviewer": use this agent after you are done writing a signficant piece of code
"greeting-responder": use this agent when to respond to user greetings with a friendly joke
</example_agent_description>

<example>
user: "Please write a function that checks if a number is prime"
assistant: Sure let me write a function that checks if a number is prime
assistant: First let me use the write_file tool to write a function that checks if a number is prime
assistant: I'm going to use the write_file tool to write the following code:
<code>
function isPrime(n) {
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {
    if (n % i === 0) return false
  }
  return true
}
</code>
<commentary>
Since a signficant piece of code was written and the task was completed, now use the code-reviewer agent to review the code
</commentary>
assistant: Now let me use the code-reviewer agent to review the code
assistant: Uses the task tool to launch the code-reviewer agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the task tool to launch the greeting-responder agent"
</example>
                    """
                f"Available agent types and the tools they have access to:\n{agent_desc}"
            )
        return "The task tool is currently unavailable because there are no registered agents."

    @property
    def input_schema(self) -> dict:
        """Input schema for task delegation.

        Supports both spawn (agent + instruction) and resume (session_id + instruction).

        Returns:
            JSON schema for the tool input with structured parameters
        """
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent name for spawning new sub-session (e.g., 'zen-architect')",
                },
                "instruction": {"type": "string", "description": "Task instruction for the agent"},
                "session_id": {
                    "type": "string",
                    "description": "Optional Session ID to resume (from previous spawn/resume response)",
                },
            },
            "required": ["instruction"],
        }

    def _get_agent_list(self) -> list[dict[str, Any]]:
        """Get list of available agents from mount plan.

        Reads agents section from the session's mount plan configuration.

        Returns:
            List of agent definitions with name and description
        """
        # Get agents from coordinator's infrastructure config property
        agents = self.coordinator.config.get("agents", {})

        sorted_agents = sorted(agents.items(), key=lambda item: item[0])
        return [{"name": name, "description": cfg.get("description", "No description")} for name, cfg in sorted_agents]

    async def execute(self, input: dict) -> ToolResult:
        """Execute delegation with structured parameters.

        Routes to spawn (new sub-session) or resume (existing sub-session)
        based on input parameters.

        Args:
            input: Dict with 'instruction' (required) and either:
                   - 'agent' (for spawn) or
                   - 'session_id' (for resume)

        Returns:
            ToolResult with success status and output or error
        """
        # Extract parameters
        agent_name = input.get("agent", "").strip()
        instruction = input.get("instruction", "").strip()
        session_id = input.get("session_id", "").strip()

        # Validate instruction (always required)
        if not instruction:
            return ToolResult(success=False, error={"message": "Instruction cannot be empty"})

        # Get hooks for error handling
        hooks = self.coordinator.get("hooks")

        # Route based on session_id presence
        if session_id:
            # RESUME MODE: Continue existing sub-session
            return await self._resume_existing_session(session_id, instruction, hooks)

        # SPAWN MODE: Create new sub-session (requires agent)
        if not agent_name:
            return ToolResult(
                success=False,
                error={"message": "Agent name required for new delegation (or provide session_id to resume)"},
            )

        # Check agent exists in registry
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

            # Emit task:agent_spawned event
            if hooks:
                await hooks.emit(
                    "task:agent_spawned",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                    },
                )

            # Spawn sub-session with agent configuration overlay
            result = await spawn_sub_session(
                agent_name=agent_name,
                instruction=instruction,
                parent_session=parent_session,
                agent_configs=agents,
                sub_session_id=sub_session_id,
            )

            # Emit task:agent_completed event
            if hooks:
                await hooks.emit(
                    "task:agent_completed",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                    },
                )

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

    async def _resume_existing_session(self, session_id: str, instruction: str, hooks) -> ToolResult:
        """Resume existing sub-session (helper for execute).

        Args:
            session_id: Sub-session ID to resume
            instruction: Follow-up instruction
            hooks: Hook coordinator for event emission

        Returns:
            ToolResult with success status and output or error
        """
        parent_session_id = self.coordinator.session_id

        try:
            # Emit task:agent_resumed event
            if hooks:
                await hooks.emit(
                    "task:agent_resumed",
                    {
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                    },
                )

            # Import resume helper (app layer - same pattern as spawn)
            from amplifier_app_cli.session_spawner import resume_sub_session

            # Resume sub-session
            result = await resume_sub_session(
                sub_session_id=session_id,
                instruction=instruction,
            )

            # Emit task:agent_completed event
            if hooks:
                await hooks.emit(
                    "task:agent_completed",
                    {
                        "sub_session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                    },
                )

            # Return output with session_id (same across turns)
            return ToolResult(
                success=True,
                output={"response": result["output"], "session_id": result["session_id"]},
            )

        except FileNotFoundError as e:
            # Session not found
            if hooks:
                await hooks.emit(
                    "tool:error",
                    {
                        "tool": "task",
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": f"Session not found: {str(e)}",
                    },
                )
            return ToolResult(
                success=False,
                error={"message": f"Session '{session_id}' not found. May have expired or never existed."},
            )

        except Exception as e:
            # Other errors (corrupted metadata, etc.)
            if hooks:
                await hooks.emit(
                    "tool:error",
                    {
                        "tool": "task",
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": str(e),
                    },
                )
            return ToolResult(success=False, error={"message": f"Resume failed: {str(e)}"})
