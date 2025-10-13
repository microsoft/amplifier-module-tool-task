"""
Task delegation tool module.
Enables AI to spawn sub-sessions for complex subtasks.
"""

import logging
from typing import Any
from typing import Optional

from amplifier_core import AmplifierSession
from amplifier_core import ModuleCoordinator
from amplifier_core import ToolResult

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the task delegation tool."""
    config = config or {}
    tool = TaskTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted TaskTool")
    return


class TaskTool:
    """
    Delegate complex tasks to sub-agents.
    Maintains isolated context while preserving key learnings.
    """

    name = "task"
    description = "Delegate a subtask to a specialized agent or sub-session"

    def __init__(self, coordinator: ModuleCoordinator, config: dict[str, Any]):
        self.coordinator = coordinator
        self.config = config
        self.max_depth = config.get("max_depth", 3)  # Prevent infinite recursion
        self.inherit_tools = config.get("inherit_tools", True)
        self.inherit_memory = config.get("inherit_memory", False)

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Description of the task to execute"},
                "agent": {"type": "string", "description": "Specific agent type to use (default: 'default')"},
                "context": {"type": "string", "description": "Additional context for the task"},
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum iterations for the sub-agent (default: 20)",
                },
                "return_transcript": {
                    "type": "boolean",
                    "description": "Include full transcript in response (default: false)",
                },
            },
            "required": ["task"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """
        Execute a subtask in an isolated context.

        Args:
            input: {
                "task": str - Description of the task to execute
                "agent": Optional[str] - Specific agent type to use
                "context": Optional[str] - Additional context for the task
                "max_iterations": Optional[int] - Override max iterations
                "return_transcript": Optional[bool] - Include full transcript
            }
        """
        task_description = input.get("task")
        if not task_description:
            return ToolResult(success=False, error={"message": "Task description is required"})

        agent_type = input.get("agent", "default")
        additional_context = input.get("context", "")
        max_iterations = input.get("max_iterations", 20)
        return_transcript = input.get("return_transcript", False)

        # Check recursion depth
        current_depth = self._get_current_depth()
        if current_depth >= self.max_depth:
            return ToolResult(success=False, error={"message": f"Maximum task depth ({self.max_depth}) reached"})

        try:
            # Emit spawn event
            hooks = self.coordinator.get("hooks")
            if hooks:
                await hooks.emit(
                    "agent:spawn", {"task": task_description, "agent": agent_type, "depth": current_depth + 1}
                )

            # Build sub-session configuration
            sub_config = self._build_sub_config(agent_type, max_iterations, current_depth + 1)

            # Create and initialize sub-session
            sub_session = AmplifierSession(sub_config)
            await sub_session.initialize()

            # Prepare the prompt with context
            prompt = self._prepare_prompt(task_description, additional_context, agent_type)

            # Execute the task
            logger.info(f"Executing subtask at depth {current_depth + 1}: {task_description[:100]}...")
            result = await sub_session.execute(prompt)

            # Clean up sub-session
            await sub_session.cleanup()

            # Emit complete event
            if hooks:
                await hooks.emit(
                    "agent:complete",
                    {"task": task_description, "agent": agent_type, "success": True, "depth": current_depth + 1},
                )

            # Prepare output
            output = {"result": result, "agent_used": agent_type, "depth": current_depth + 1}

            if return_transcript:
                # Get transcript from sub-session context
                output["transcript"] = await self._get_transcript(sub_session)

            return ToolResult(success=True, output=output)

        except Exception as e:
            logger.error(f"Task execution failed: {e}")

            # Emit failure event
            if hooks:
                await hooks.emit(
                    "agent:complete",
                    {
                        "task": task_description,
                        "agent": agent_type,
                        "success": False,
                        "error": str(e),
                        "depth": current_depth + 1,
                    },
                )

            return ToolResult(success=False, error={"message": f"Task failed: {str(e)}"})

    def _get_current_depth(self) -> int:
        """Determine current recursion depth from context."""
        # Check if we're running inside a sub-session by looking at metadata
        # This would check context or environment for depth indicator
        # For now, we'll check the coordinator for metadata
        try:
            # Look for metadata that might indicate depth
            context = self.coordinator.get("context")
            if context and hasattr(context, "metadata"):
                return context.metadata.get("task_depth", 0)
        except Exception:
            pass
        return 0

    def _build_sub_config(self, agent_type: str, max_iterations: int, depth: int) -> dict[str, Any]:
        """Build configuration for sub-session."""

        # Start with current session's base config
        base_config = {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            "context": {"config": {"max_tokens": 50_000, "compact_threshold": 0.9}},  # Smaller context for subtasks
            "providers": [],
            "tools": [],
            "agents": [],
            "hooks": [],
        }

        # Copy providers from parent
        providers = self.coordinator.get("providers")
        if providers:
            for name, provider in providers.items():
                base_config["providers"].append({"module": f"provider-{name}", "instance": provider})  # Reuse instance

        # Selectively copy tools
        if self.inherit_tools:
            tools = self.coordinator.get("tools")
            if tools:
                for name, tool in tools.items():
                    # Don't include 'task' tool to prevent deep recursion
                    if name != "task" or depth < self.max_depth - 1:
                        base_config["tools"].append({"module": f"tool-{name}", "instance": tool})  # Reuse instance

        # Configure for specific agent type
        if agent_type != "default":
            agent_config = self._get_agent_config(agent_type)
            if agent_config:
                base_config.update(agent_config)

        # Add depth indicator
        base_config["metadata"] = {"task_depth": depth, "parent_session": True}

        # Set iteration limit
        if "orchestrator_config" not in base_config:
            base_config["orchestrator_config"] = {}
        base_config["orchestrator_config"]["max_iterations"] = max_iterations

        return base_config

    def _prepare_prompt(self, task: str, context: str, agent_type: str) -> str:
        """Prepare the prompt for the sub-agent."""

        prompt_parts = []

        # Add agent-specific instructions
        if agent_type == "architect":
            prompt_parts.append(
                "You are executing as a specialized architect agent. Focus on system design with ruthless simplicity."
            )
        elif agent_type == "researcher":
            prompt_parts.append(
                "You are executing as a research agent. Gather comprehensive information and provide detailed analysis."
            )
        elif agent_type == "implementer":
            prompt_parts.append(
                "You are executing as an implementation agent. "
                "Focus on writing clean, functional code that solves the problem."
            )
        elif agent_type == "reviewer":
            prompt_parts.append(
                "You are executing as a review agent. "
                "Analyze the code or design for issues, suggest improvements, and ensure quality."
            )

        # Add context if provided
        if context:
            prompt_parts.append(f"Context:\n{context}")

        # Add the task
        prompt_parts.append(f"Task:\n{task}")

        # Add completion instruction
        prompt_parts.append(
            "\nComplete this task to the best of your ability. Be thorough but concise in your response."
        )

        return "\n\n".join(prompt_parts)

    def _get_agent_config(self, agent_type: str) -> dict[str, Any] | None:
        """Get specific configuration for an agent type."""

        agent_configs = {
            "architect": {
                "orchestrator_config": {
                    "temperature": 0.7,
                    "system_prompt": "You are a zen architect valuing simplicity.",
                }
            },
            "researcher": {"tools": [{"module": "tool-web"}, {"module": "tool-search"}]},
            "implementer": {
                "tools": [{"module": "tool-filesystem"}, {"module": "tool-bash"}],
                "orchestrator_config": {"temperature": 0.3},  # Lower temp for code generation
            },
            "reviewer": {
                "tools": [{"module": "tool-filesystem"}, {"module": "tool-grep"}],
                "orchestrator_config": {
                    "system_prompt": "You are a code reviewer. Find issues and suggest improvements."
                },
            },
        }

        return agent_configs.get(agent_type)

    async def _get_transcript(self, session: AmplifierSession) -> list[dict]:
        """Extract transcript from completed session."""
        try:
            context = session.coordinator.get("context")
            if context:
                messages = await context.get_messages()
                return messages
        except Exception as e:
            logger.warning(f"Could not get transcript: {e}")
        return []
