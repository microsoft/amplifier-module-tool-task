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

Config Options:
- exclude_tools: List of tools spawned agents should NOT receive (e.g., ["tool-task"])
- inherit_tools: List of tools spawned agents SHOULD receive (mutually exclusive with exclude_tools)
- exclude_hooks: List of hooks spawned agents should NOT receive (e.g., ["hooks-logging"])
- inherit_hooks: List of hooks spawned agents SHOULD receive (mutually exclusive with exclude_hooks)
- max_recursion_depth: Maximum depth for nested delegations (default: 1)

Tool Parameters (caller-controlled):
- inherit_context: Context inheritance mode - "none" (default), "recent", or "all"
- inherit_context_turns: Number of recent turns when inherit_context is "recent" (default: 5)
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import logging
import re
import uuid
from typing import Any

from amplifier_core import ModuleCoordinator
from amplifier_core import ToolResult
from amplifier_foundation import ProviderPreference

logger = logging.getLogger(__name__)


def _sanitize_agent_name(name: str) -> str:
    """Sanitize agent name for filesystem-safe session IDs.

    Agent names like 'foundation:foundation-expert' contain colons which are
    invalid in Windows filenames. This function replaces non-alphanumeric
    characters with hyphens to ensure cross-platform compatibility.

    Args:
        name: Raw agent name (e.g., 'foundation:zen-architect')

    Returns:
        Sanitized name safe for use in file paths (e.g., 'foundation-zen-architect')
    """
    # Convert to lowercase and replace non-alphanumeric with hyphens
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
    # Collapse multiple consecutive hyphens
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip("-")
    # Default to "agent" if empty after sanitization
    return sanitized or "agent"


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the task delegation tool.

    Args:
        coordinator: The module coordinator
        config: Optional configuration with:
            - exclude_tools: Tools spawned agents should NOT inherit (e.g., ["tool-task"])
            - inherit_tools: Tools spawned agents SHOULD inherit (mutually exclusive with exclude_tools)
            - exclude_hooks: Hooks spawned agents should NOT inherit (e.g., ["hooks-logging"])
            - inherit_hooks: Hooks spawned agents SHOULD inherit (mutually exclusive with exclude_hooks)
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
                - exclude_tools: Tools spawned agents should NOT inherit
                - inherit_tools: Tools spawned agents SHOULD inherit
                - exclude_hooks: Hooks spawned agents should NOT inherit
                - inherit_hooks: Hooks spawned agents SHOULD inherit
                - max_recursion_depth: Max delegation depth (default: 1)
        """
        self.coordinator = coordinator
        self.config = config

        # Tool inheritance settings (mutually exclusive)
        self.exclude_tools: list[str] = config.get("exclude_tools", [])
        self.inherit_tools: list[str] | None = config.get(
            "inherit_tools"
        )  # None means inherit all

        # Hook inheritance settings (mutually exclusive)
        self.exclude_hooks: list[str] = config.get("exclude_hooks", [])
        self.inherit_hooks: list[str] | None = config.get(
            "inherit_hooks"
        )  # None means inherit all

    @property
    def description(self) -> str:
        """Generate dynamic description with available agents.

        Queries the agent registry to provide an up-to-date list
        of available agents in the tool description.
        """
        agents_list = self._get_agent_list()
        if agents_list:
            agent_desc = "\n".join(
                f"  - {a['name']}: {a.get('description', 'No description')}"
                for a in agents_list
            )
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
"code-reviewer": use this agent after you are done writing a significant piece of code
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
Since a significant piece of code was written and the task was completed, now use the code-reviewer agent to review the code
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
                    "description": "Agent name for spawning new sub-session (e.g., 'developer-expertise:zen-architect')",
                },
                "instruction": {
                    "type": "string",
                    "description": "Task instruction for the agent",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional Session ID to resume (from previous spawn/resume response)",
                },
                "inherit_context": {
                    "type": "string",
                    "enum": ["none", "recent", "all"],
                    "description": "Context inheritance mode: 'none' (default) - child starts fresh, 'recent' - pass last N turns, 'all' - pass full conversation history",
                },
                "inherit_context_turns": {
                    "type": "integer",
                    "description": "Number of recent turns to pass when inherit_context is 'recent' (default: 5)",
                },
                "provider_preferences": {
                    "type": "array",
                    "description": "Ordered list of provider/model preferences. System tries each until one is available. Model names support glob patterns (e.g., 'claude-haiku-*').",
                    "items": {
                        "type": "object",
                        "properties": {
                            "provider": {
                                "type": "string",
                                "description": "Provider name (e.g., 'anthropic', 'openai')",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model name or glob pattern (e.g., 'claude-haiku-*', 'gpt-4o-mini')",
                            },
                        },
                        "required": ["provider", "model"],
                    },
                },
            },
            "required": ["instruction"],
        }

    async def _extract_parent_messages(
        self, inherit_context: str, inherit_context_turns: int
    ) -> list[dict[str, Any]] | None:
        """Extract messages from parent session based on inheritance policy.

        Args:
            inherit_context: Inheritance mode - "none", "recent", or "all"
            inherit_context_turns: Number of recent turns to include (for "recent" mode)

        Returns:
            List of messages to pass to child session, or None if inherit_context is "none"
        """
        if inherit_context == "none":
            return None

        # Get parent's context manager
        parent_context = self.coordinator.get("context")
        if not parent_context or not hasattr(parent_context, "get_messages"):
            logger.debug("No parent context available for inheritance")
            return None

        try:
            messages = await parent_context.get_messages()
            if not messages:
                return None

            if inherit_context == "all":
                # Return all messages, sanitized for child consumption
                return self._sanitize_messages_for_child(messages)

            elif inherit_context == "recent":
                # Extract last N turns (a turn is a user->assistant exchange)
                recent_messages = self._extract_recent_turns(
                    messages, inherit_context_turns
                )
                return self._sanitize_messages_for_child(recent_messages)

            return None

        except Exception as e:
            logger.warning(f"Failed to extract parent messages: {e}")
            return None

    def _extract_recent_turns(
        self, messages: list[dict[str, Any]], n_turns: int
    ) -> list[dict[str, Any]]:
        """Extract the last N user->assistant turns from messages.

        A "turn" starts with a user message and includes all subsequent messages
        until the next user message.

        Args:
            messages: Full message history
            n_turns: Number of recent turns to extract

        Returns:
            Messages from the last N turns
        """
        if not messages or n_turns <= 0:
            return []

        # Find indices where user messages start (turn boundaries)
        turn_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]

        if not turn_starts:
            return messages  # No user messages, return all

        if len(turn_starts) <= n_turns:
            return messages  # Fewer turns than requested, return all

        # Get messages from the nth-to-last turn onwards
        start_index = turn_starts[-n_turns]
        return messages[start_index:]

    def _sanitize_messages_for_child(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sanitize messages for safe injection into child session.

        Strips non-essential fields and ensures message format compatibility.
        Only includes user and assistant messages (skips system/tool messages
        as those are context-specific to the parent session).

        IMPORTANT: This sanitization handles multiple message formats:
        1. Anthropic's content block format (content as list with tool_use blocks)
        2. Amplifier's internal format (tool_calls field at message level)
        3. Tool result messages (role="tool" or has tool_call_id)

        All tool-related data is stripped since child sessions won't have
        the matching tool context.

        Args:
            messages: Raw messages from parent

        Returns:
            Sanitized messages suitable for child context
        """
        sanitized = []
        for msg in messages:
            role = msg.get("role")

            # Skip tool result messages entirely (role="tool" in some formats)
            if role == "tool":
                continue

            # Skip messages that are tool results (have tool_call_id)
            if msg.get("tool_call_id"):
                continue

            # Only pass user and assistant messages - system prompts are
            # context-specific and shouldn't be inherited
            if role in ("user", "assistant"):
                # Skip assistant messages that ONLY contain tool calls
                # (these are just tool invocations with no meaningful text)
                if (
                    role == "assistant"
                    and msg.get("tool_calls")
                    and not msg.get("content")
                ):
                    continue

                content = msg.get("content", "")
                sanitized_content = self._sanitize_content(content)

                # Only include message if it has content after sanitization
                if sanitized_content:
                    # Create a clean message with ONLY role and content
                    # Do NOT copy tool_calls or any other fields
                    sanitized_msg = {"role": role, "content": sanitized_content}
                    sanitized.append(sanitized_msg)
        return sanitized

    def _sanitize_content(self, content: Any) -> str | list[dict[str, Any]]:
        """Sanitize message content, handling both string and list formats.

        Content formats that need to be handled:
        - A simple string: "Hello, how can I help?"
        - Anthropic API format: [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
        - Amplifier internal format: [{"type": "tool_call", ...}, {"type": "text", ...}]

        This method filters out ALL tool-related blocks and extracts only text content.
        Tool-related block types to filter:
        - tool_use (Anthropic's format for tool calls)
        - tool_call (Amplifier's internal format for tool calls)
        - tool_result (both Anthropic and Amplifier format for tool results)
        - thinking (internal reasoning blocks)

        Args:
            content: Message content (string or list of content blocks)

        Returns:
            Sanitized content as string (preferred) or list of text blocks
        """
        # Handle simple string content
        if isinstance(content, str):
            return content

        # Handle list of content blocks
        if isinstance(content, list):
            text_parts = []
            # Types to explicitly filter out (tool-related and internal)
            filtered_types = {
                "tool_use",  # Anthropic tool call format
                "tool_call",  # Amplifier internal tool call format
                "tool_result",  # Tool results
                "thinking",  # Internal reasoning blocks
                "redacted_thinking",  # Redacted thinking blocks
            }

            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    # Only keep text blocks, filter out everything else
                    if block_type == "text":
                        text = block.get("text", "")
                        if text:
                            text_parts.append(text)
                    elif block_type in filtered_types:
                        # Explicitly skip these - they're tool or internal blocks
                        pass
                    else:
                        # For unknown types, try to extract any text content
                        # but log a warning in case we should handle it
                        logger.debug(
                            f"Unknown content block type '{block_type}' in message sanitization"
                        )
                elif isinstance(block, str):
                    # Sometimes content blocks can be plain strings
                    text_parts.append(block)

            # Return as single string if we have text
            if text_parts:
                return "\n".join(text_parts)

        # Empty or unrecognized format
        return ""

    def _format_parent_context_for_instruction(
        self, messages: list[dict[str, Any]]
    ) -> str:
        """Format parent messages as text to prepend to the instruction.

        This ensures the child agent sees the parent context regardless of
        how the session/orchestrator handles pre-existing context messages.

        Args:
            messages: List of sanitized messages from parent session

        Returns:
            Formatted text block with parent conversation context
        """
        if not messages:
            return ""

        lines = ["[PARENT CONVERSATION CONTEXT]"]
        lines.append(
            "The following is recent conversation history from the parent session:"
        )
        lines.append("")

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Format role nicely
            role_label = role.upper()
            if role == "user":
                role_label = "USER"
            elif role == "assistant":
                role_label = "ASSISTANT"

            # Truncate very long messages to avoid overwhelming the child
            max_content_len = 2000
            if len(content) > max_content_len:
                content = content[:max_content_len] + "... [truncated]"

            lines.append(f"{role_label}: {content}")
            lines.append("")

        lines.append("[END PARENT CONTEXT]")
        return "\n".join(lines)

    def _get_agent_list(self) -> list[dict[str, Any]]:
        """Get list of available agents from mount plan.

        Reads agents section from the session's mount plan configuration.

        Returns:
            List of agent definitions with name and description
        """
        # Get agents from coordinator's infrastructure config property
        agents = self.coordinator.config.get("agents", {})

        sorted_agents = sorted(agents.items(), key=lambda item: item[0])
        return [
            {"name": name, "description": cfg.get("description", "No description")}
            for name, cfg in sorted_agents
        ]

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

        # Context inheritance parameters (caller-controlled)
        inherit_context = input.get("inherit_context", "none")
        inherit_context_turns = input.get("inherit_context_turns", 5)

        # Provider preferences (caller-controlled) - ordered fallback chain
        raw_provider_prefs = input.get("provider_preferences", [])
        provider_preferences = None
        if raw_provider_prefs:
            provider_preferences = [
                ProviderPreference.from_dict(p) for p in raw_provider_prefs
            ]

        # Validate instruction (always required)
        if not instruction:
            return ToolResult(
                success=False, error={"message": "Instruction cannot be empty"}
            )

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
                error={
                    "message": "Agent name required for new delegation (or provide session_id to resume)"
                },
            )

        # Check agent exists in registry
        agents = self.coordinator.config.get("agents", {})
        if agent_name not in agents:
            return ToolResult(
                success=False, error={"message": f"Agent '{agent_name}' not found"}
            )

        # Note: Recursion depth limiting not yet implemented
        # Future: Track depth in metadata and check against config.max_recursion_depth

        # Get parent session ID from coordinator infrastructure
        parent_session_id = self.coordinator.session_id

        # Generate hierarchical sub-session ID using W3C Trace Context format
        # Format: {parent-span}-{child-span}_{agent-name}
        # Underscore separator enables streaming UI to parse agent name
        child_span = uuid.uuid4().hex[:16]  # 16-char child span ID
        # Sanitize agent name for cross-platform filesystem compatibility
        # (Windows doesn't allow colons in filenames)
        sanitized_agent = _sanitize_agent_name(agent_name)
        sub_session_id = f"{parent_session_id}-{child_span}_{sanitized_agent}"

        # Get hooks for error handling (orchestrator will emit tool:pre/post)
        hooks = self.coordinator.get("hooks")

        try:
            # Get spawn capability (registered by app layer)
            spawn_fn = self.coordinator.get_capability("session.spawn")
            if spawn_fn is None:
                return ToolResult(
                    success=False,
                    error={
                        "message": "Session spawning not available. App layer must register 'session.spawn' capability."
                    },
                )

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

            # Build tool inheritance policy from config
            tool_inheritance = {}
            if self.exclude_tools:
                tool_inheritance["exclude_tools"] = self.exclude_tools
            elif self.inherit_tools is not None:
                tool_inheritance["inherit_tools"] = self.inherit_tools

            # Build hook inheritance policy from config
            hook_inheritance = {}
            if self.exclude_hooks:
                hook_inheritance["exclude_hooks"] = self.exclude_hooks
            elif self.inherit_hooks is not None:
                hook_inheritance["inherit_hooks"] = self.inherit_hooks

            # Extract parent messages based on context inheritance policy (caller-controlled)
            parent_messages = await self._extract_parent_messages(
                inherit_context, inherit_context_turns
            )

            # Format parent context into instruction if messages were extracted
            # This ensures the child agent sees the parent context regardless of
            # how the session/orchestrator handles pre-existing context messages
            effective_instruction = instruction
            if parent_messages:
                logger.debug(
                    f"Extracted {len(parent_messages)} parent messages for child session"
                )
                context_text = self._format_parent_context_for_instruction(
                    parent_messages
                )
                effective_instruction = f"{context_text}\n\n[YOUR TASK]\n{instruction}"

            # Extract orchestrator config from parent session for inheritance
            # This ensures rate limiting and other orchestrator settings propagate to child sessions
            orchestrator_config = None
            parent_config = parent_session.config or {}
            session_config = parent_config.get("session", {})
            orch_section = session_config.get("orchestrator", {})
            if orch_config := orch_section.get("config"):
                orchestrator_config = orch_config
                logger.debug(
                    f"Inheriting orchestrator config from parent: {orchestrator_config}"
                )

            # Spawn sub-session with agent configuration overlay
            result = await spawn_fn(
                agent_name=agent_name,
                instruction=effective_instruction,
                parent_session=parent_session,
                agent_configs=agents,
                sub_session_id=sub_session_id,
                tool_inheritance=tool_inheritance,
                hook_inheritance=hook_inheritance,
                orchestrator_config=orchestrator_config,
                provider_preferences=provider_preferences,
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
                output={
                    "response": result["output"],
                    "session_id": result["session_id"],
                },
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

            return ToolResult(
                success=False, error={"message": f"Delegation failed: {str(e)}"}
            )

    async def _resume_existing_session(
        self, session_id: str, instruction: str, hooks
    ) -> ToolResult:
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

            # Get resume capability (registered by app layer)
            resume_fn = self.coordinator.get_capability("session.resume")
            if resume_fn is None:
                return ToolResult(
                    success=False,
                    error={
                        "message": "Session resumption not available. App layer must register 'session.resume' capability."
                    },
                )

            # Resume sub-session
            result = await resume_fn(
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
                output={
                    "response": result["output"],
                    "session_id": result["session_id"],
                },
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
                error={
                    "message": f"Session '{session_id}' not found. May have expired or never existed."
                },
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
            return ToolResult(
                success=False, error={"message": f"Resume failed: {str(e)}"}
            )
