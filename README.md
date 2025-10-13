# amplifier-module-tool-task

Task delegation tool for Amplifier that enables AI agents to spawn sub-sessions for complex subtasks.

## Purpose

This is the MOST CRITICAL missing feature for autonomous coding capability. It allows AI agents to:
- Delegate complex subtasks to specialized sub-agents
- Maintain isolated contexts for each subtask
- Preserve key learnings while preventing context pollution
- Support different agent types (architect, researcher, implementer, reviewer)

## Features

- **Recursive Session Spawning**: Create isolated AmplifierSession instances for subtasks
- **Context Isolation**: Each subtask runs in its own context window (50K tokens)
- **Depth Limiting**: Prevents infinite recursion (configurable, default max_depth=3)
- **Tool Inheritance**: Sub-sessions can inherit parent tools (optional)
- **Agent Specialization**: Support for different agent types with tailored configurations
- **Hook Integration**: Emits agent:spawn and agent:complete events

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
    max_depth = 3,           # Maximum recursion depth
    inherit_tools = true,    # Sub-sessions inherit parent tools
    inherit_memory = false   # Sub-sessions don't inherit memory (not implemented)
}
```

## Usage

The tool is automatically available to AI agents once mounted. The AI can use it like:

```python
# Delegate a complex analysis task
result = await task_tool.execute({
    "task": "Analyze the authentication system architecture",
    "agent": "architect",
    "context": "Focus on security and simplicity",
    "max_iterations": 15,
    "return_transcript": False
})

# Delegate research task
result = await task_tool.execute({
    "task": "Research best practices for JWT token management",
    "agent": "researcher"
})

# Delegate implementation task
result = await task_tool.execute({
    "task": "Implement the user registration endpoint",
    "agent": "implementer",
    "context": "Use existing auth utilities"
})

# Delegate code review
result = await task_tool.execute({
    "task": "Review the recent changes for security issues",
    "agent": "reviewer"
})
```

## Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| task | string | Yes | Description of the subtask to execute |
| agent | string | No | Agent type: default, architect, researcher, implementer, reviewer |
| context | string | No | Additional context for the task |
| max_iterations | integer | No | Override max iterations (default 20) |
| return_transcript | boolean | No | Include full conversation transcript (default False) |

## Output Format

```json
{
    "success": true,
    "output": {
        "result": "The analysis shows that...",
        "agent_used": "architect",
        "depth": 1,
        "transcript": [...]  // Only if return_transcript=true
    }
}
```

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

The tool emits two types of events:

### agent:spawn
Emitted before executing a subtask:
```json
{
    "task": "task description",
    "agent": "agent_type",
    "depth": 1
}
```

### agent:complete
Emitted after task completion (success or failure):
```json
{
    "task": "task description",
    "agent": "agent_type",
    "success": true,
    "depth": 1,
    "error": "error message"  // Only on failure
}
```

## Philosophy

This tool follows the "Ruthless Simplicity" principle:
- Synchronous execution (no complex scheduling)
- Direct use of AmplifierSession
- Clear, meaningful error messages
- Fail-fast validation

## Future Enhancements

- Memory inheritance between sessions
- Custom agent type definitions
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
