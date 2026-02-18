"""Tests for agent name sanitization (Windows path compatibility).

These tests verify that agent names are properly sanitized for use in
file paths across all platforms, specifically addressing the Windows
limitation that colons (':') are not allowed in filenames.
"""

from __future__ import annotations


from amplifier_module_tool_task import _sanitize_agent_name


class TestSanitizeAgentName:
    """Tests for _sanitize_agent_name function."""

    def test_colon_replaced_with_hyphen(self) -> None:
        """Colons in agent names are replaced with hyphens.

        This is the primary fix for Windows compatibility where colons
        are reserved for drive letters (e.g., C:).
        """
        result = _sanitize_agent_name("foundation:foundation-expert")
        assert result == "foundation-foundation-expert"
        assert ":" not in result

    def test_bundle_agent_format(self) -> None:
        """Bundle:agent format is sanitized correctly."""
        result = _sanitize_agent_name("amplifier:amplifier-expert")
        assert result == "amplifier-amplifier-expert"

    def test_multiple_colons(self) -> None:
        """Multiple colons are all replaced."""
        result = _sanitize_agent_name("a:b:c:d")
        assert result == "a-b-c-d"

    def test_preserves_existing_hyphens(self) -> None:
        """Existing hyphens in agent names are preserved."""
        result = _sanitize_agent_name("zen-architect")
        assert result == "zen-architect"

    def test_collapses_multiple_hyphens(self) -> None:
        """Multiple consecutive hyphens are collapsed to one."""
        result = _sanitize_agent_name("test---agent")
        assert result == "test-agent"

    def test_removes_leading_trailing_hyphens(self) -> None:
        """Leading and trailing hyphens are removed."""
        result = _sanitize_agent_name("-test-agent-")
        assert result == "test-agent"

    def test_converts_to_lowercase(self) -> None:
        """Agent names are converted to lowercase."""
        result = _sanitize_agent_name("MyAgent")
        assert result == "myagent"

    def test_special_characters_replaced(self) -> None:
        """All non-alphanumeric characters are replaced with hyphens."""
        # Windows-invalid characters: < > : " / \ | ? *
        result = _sanitize_agent_name('test<>:"/\\|?*agent')
        assert result == "test-agent"
        # Verify no Windows-invalid characters remain
        for char in '<>:"/\\|?*':
            assert char not in result

    def test_spaces_replaced(self) -> None:
        """Spaces are replaced with hyphens."""
        result = _sanitize_agent_name("my agent name")
        assert result == "my-agent-name"

    def test_underscores_replaced(self) -> None:
        """Underscores are replaced with hyphens for consistency."""
        result = _sanitize_agent_name("my_agent_name")
        assert result == "my-agent-name"

    def test_empty_string_returns_default(self) -> None:
        """Empty string returns 'agent' as default."""
        result = _sanitize_agent_name("")
        assert result == "agent"

    def test_only_special_chars_returns_default(self) -> None:
        """String with only special characters returns 'agent' default."""
        result = _sanitize_agent_name(":::")
        assert result == "agent"

    def test_numbers_preserved(self) -> None:
        """Numbers in agent names are preserved."""
        result = _sanitize_agent_name("agent123")
        assert result == "agent123"

    def test_real_world_agent_names(self) -> None:
        """Test real-world agent name patterns from Amplifier ecosystem."""
        test_cases = [
            ("foundation:zen-architect", "foundation-zen-architect"),
            ("foundation:bug-hunter", "foundation-bug-hunter"),
            ("foundation:explorer", "foundation-explorer"),
            ("foundation:git-ops", "foundation-git-ops"),
            ("recipes:recipe-author", "recipes-recipe-author"),
            ("amplifier:amplifier-expert", "amplifier-amplifier-expert"),
            ("core:core-expert", "core-core-expert"),
            ("python-dev:python-dev", "python-dev-python-dev"),
        ]
        for input_name, expected in test_cases:
            result = _sanitize_agent_name(input_name)
            assert result == expected, f"Failed for {input_name}: got {result}"

    def test_result_is_filesystem_safe(self) -> None:
        """Verify result contains only filesystem-safe characters."""
        # Test with a variety of problematic inputs
        problematic_inputs = [
            "test:agent",
            "test/agent",
            "test\\agent",
            "test<agent>",
            'test"agent"',
            "test|agent",
            "test?agent",
            "test*agent",
            "test\tagent",
            "test\nagent",
        ]
        for input_name in problematic_inputs:
            result = _sanitize_agent_name(input_name)
            # Result should only contain lowercase letters, numbers, and hyphens
            assert all(c.islower() or c.isdigit() or c == "-" for c in result), (
                f"Unsafe characters in result for {input_name!r}: {result}"
            )
