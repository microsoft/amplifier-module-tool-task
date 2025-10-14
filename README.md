# amplifier-module-tool-task

Task delegation tool for Amplifier that enables AI agents to spawn sub-sessions for complex subtasks.

## Purpose

This is the MOST CRITICAL missing feature for autonomous coding capability. It allows AI agents to:
- Delegate complex subtasks to specialized sub-agents
- Maintain isolated contexts for each subtask
- Preserve key learnings while preventing context pollution
- Support different agent types (architect, researcher, implementer, reviewer)

## Features

- **Simple String Interface**: Uses "agent: instruction" format for ease of use
- **Dynamic Agent Discovery**: Queries available agents from registry
- **Depth Limiting**: Prevents infinite recursion (configurable max_depth)
- **Proper Event Emission**: Emits tool:pre, tool:post, tool:error with sub_session_id
- **Parent/Child Linking**: Tracks session relationships via IDs
- **Kernel Philosophy Compliance**: Pure mechanism, no policy decisions

## Prerequisites

- **Python 3.11+**
- **[UV](https://github.com/astral-sh/uv)** - Fast Python package manager

### Installing UV

```bash
# macOS/Linux/WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Installation

```bash
cd amplifier-next/amplifier-module-tool-task
uv pip install -e .
```

## Configuration

Add to your Amplifier config:

```toml
[[tools]]
module = "tool-task"
config = {
    max_recursion_depth = 1  # Maximum recursion depth (default: 1)
}
```

## Usage

The tool is automatically available to AI agents once mounted. The AI uses a simple string format:

```
agent_name: instruction
```

Examples:
- `architect: Analyze the authentication system architecture`
- `researcher: Research best practices for JWT token management`
- `implementer: Implement the user registration endpoint`
- `reviewer: Review the recent changes for security issues`

## Input Format

The tool accepts a single string input in the format:
```
agent_name: instruction
```

Where:
- `agent_name`: The name of the agent to delegate to (must exist in agent registry)
- `instruction`: The task or question for the agent to handle

## Output Format

On success:
```json
{
    "success": true,
    "output": "[Would delegate to agent_name]: instruction"
}
```

Note: The actual sub-session spawning is handled by the app layer. This tool provides the mechanism (event emission, validation) while the policy (how to spawn sessions) lives at the edges.

## Agent Types

### default
Standard agent with no special configuration. Inherits parent tools and providers.

### architect
- Temperature: 0.7
- System prompt: "You are a zen architect valuing simplicity"
- Focus: System design and architecture decisions

### researcher
- Tools: Web and search tools only
- Focus: Information gathering and analysis

### implementer
- Tools: Filesystem and bash tools
- Temperature: 0.3 (lower for code generation)
- Focus: Writing clean, functional code

### reviewer
- Tools: Filesystem and grep tools
- System prompt: "You are a code reviewer. Find issues and suggest improvements"
- Focus: Code quality and security review

## Error Handling

The tool handles several error cases:
- Missing task description
- Maximum recursion depth exceeded
- Sub-session initialization failures
- Task execution failures

All errors are logged and returned with descriptive messages.

## Hook Events

The tool emits standard kernel events:

### tool:pre
Emitted before delegation attempt:
```json
{
    "tool": "task",
    "agent": "agent_name",
    "instruction": "instruction text",
    "sub_session_id": "s-uuid",
    "parent_session_id": "parent-id",
    "depth": 1
}
```

### tool:post
Emitted after successful delegation:
```json
{
    "tool": "task",
    "agent": "agent_name",
    "sub_session_id": "s-uuid",
    "parent_session_id": "parent-id",
    "status": "ok"
}
```

### tool:error
Emitted on delegation failure:
```json
{
    "tool": "task",
    "agent": "agent_name",
    "sub_session_id": "s-uuid",
    "parent_session_id": "parent-id",
    "error": "error message"
}
```

## Philosophy

This tool follows kernel philosophy principles:
- **Mechanism, not policy**: Provides delegation mechanism, app layer decides how to spawn
- **Simple interfaces**: Single string input "agent: instruction"
- **Event-first**: Emits proper kernel events for observability
- **Clean boundaries**: No direct session creation, uses mount points and capabilities
- **Fail-fast validation**: Clear, meaningful error messages

## Future Enhancements

- Full session.spawn capability implementation in app layer
- Memory inheritance between sessions
- Task result caching
- Parallel task execution
- Advanced context management

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
