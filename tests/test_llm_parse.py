"""Tests for JSON code-fence extraction and LLM response parsing (#148)."""

import json
import textwrap
import unittest

from calcore.llm import _extract_json, parse_adjustment_plan


class ExtractJsonTests(unittest.TestCase):
    """Tests for the _extract_json utility function."""

    def test_raw_json_passes_through(self):
        """JSON with no fences returns as-is."""
        obj = {"key": "value"}
        raw = json.dumps(obj)
        self.assertEqual(_extract_json(raw), raw)

    def test_lowercase_fence_with_json_tag(self):
        """```json ... ``` extracts inner content."""
        raw = "```json\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_uppercase_fence_json_tag(self):
        """```JSON ... ``` extracts inner content (case-insensitive)."""
        raw = "```JSON\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_mixed_case_fence_json_tag(self):
        """```Json ... ``` also works."""
        raw = "```Json\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_fence_without_language_tag(self):
        """``` ... ``` with no language tag still works."""
        raw = "```\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_fence_with_prose_prefix(self):
        """Prose before the fence is handled."""
        raw = "Here's the plan:\n```json\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_fence_with_newline_before_json_tag(self):
        """Newline between fence and language tag."""
        raw = "```\njson\n{\"key\": \"value\"}\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_nested_braces_in_reason_field(self):
        """JSON with nested braces (e.g. in a reason string) is balanced correctly."""
        plan = json.dumps(
            {
                "adjustments": [],
                "next_step": "verify",
                "confidence": 0.5,
                "meta": {"outer": {"inner": "value"}},
            }
        )
        raw = "```json\n" + plan + "\n```"
        result = _extract_json(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed["meta"]["outer"]["inner"], "value")

    def test_brace_balancing_fallback_no_fence(self):
        """When no fence is found, brace balancing extracts JSON from mixed text."""
        raw = "Some preamble text\n{\"key\": \"value\"} trailing text"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_brace_balancing_with_nested_braces(self):
        """Brace balancing handles nested objects."""
        raw = "text {\"outer\": {\"inner\": \"value\"}} more text"
        result = _extract_json(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed["outer"]["inner"], "value")

    def test_no_braces_returns_stripped_text(self):
        """When no braces found, returns stripped input."""
        self.assertEqual(_extract_json("  hello world  "), "hello world")

    def test_fence_with_extra_whitespace(self):
        """Extra whitespace inside fence is stripped."""
        raw = "```json\n\n  {\"key\": \"value\"}  \n\n```"
        self.assertEqual(_extract_json(raw), '{"key": "value"}')

    def test_multiple_fences_returns_first_match(self):
        """When multiple fences exist, the first one is extracted."""
        raw = "```json\n{\"a\": 1}\n```\n```json\n{\"b\": 2}\n```"
        self.assertEqual(_extract_json(raw), '{"a": 1}')

    def test_whitespace_only_fence_content(self):
        """Fence with only whitespace inside returns empty string."""
        raw = "```json\n   \n  \n```"
        self.assertEqual(_extract_json(raw), "")

    def test_raw_json_with_leading_whitespace(self):
        """Raw JSON with leading whitespace is stripped."""
        raw = "  \n  {\"key\": \"value\"}  "
        self.assertEqual(_extract_json(raw), '{"key": "value"}')


class ParseAdjustmentPlanFenceTests(unittest.TestCase):
    """Tests for parse_adjustment_plan with various fence formats (#148)."""

    def _make_valid_plan(self):
        return json.dumps(
            {
                "adjustments": [
                    {
                        "menu": "White Balance",
                        "setting": "B-Gain",
                        "from": 0,
                        "to": -3,
                        "scope": "global",
                        "reason": "Blue push at 80% lifting xy above D65 locus.",
                    }
                ],
                "next_step": "rerun_grayscale",
                "confidence": 0.85,
            }
        )

    def test_lowercase_fence_still_works(self):
        """Existing test: ```json ... ``` should still parse."""
        plan_json = self._make_valid_plan()
        fenced = f"```json\n{plan_json}\n```"
        result = parse_adjustment_plan(fenced)
        self.assertIsNotNone(result)
        self.assertEqual(result.next_step, "rerun_grayscale")

    def test_uppercase_fence_json_tag(self):
        """```JSON ... ``` should parse correctly (was broken before #148)."""
        plan_json = self._make_valid_plan()
        fenced = f"```JSON\n{plan_json}\n```"
        result = parse_adjustment_plan(fenced)
        self.assertIsNotNone(result)
        self.assertEqual(result.next_step, "rerun_grayscale")

    def test_fence_without_language_tag(self):
        """``` ... ``` with no language tag should parse."""
        plan_json = self._make_valid_plan()
        fenced = f"```\n{plan_json}\n```"
        result = parse_adjustment_plan(fenced)
        self.assertIsNotNone(result)

    def test_prose_before_fence(self):
        """Text before the code fence should be handled."""
        plan_json = self._make_valid_plan()
        raw = f"Here's my adjustment plan:\n```json\n{plan_json}\n```"
        result = parse_adjustment_plan(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.next_step, "rerun_grayscale")

    def test_newline_before_json_tag(self):
        """Newline between fence and json tag."""
        plan_json = self._make_valid_plan()
        raw = f"```\njson\n{plan_json}\n```"
        result = parse_adjustment_plan(raw)
        self.assertIsNotNone(result)

    def test_nested_braces_in_reason_field(self):
        """Plan with nested braces in reason field should parse."""
        plan = {
            "adjustments": [
                {
                    "menu": "CMS",
                    "setting": "Saturation",
                    "from": 100,
                    "to": 80,
                    "scope": "local",
                    "reason": "Primary red has {hue_error: 1.2, chroma_error: 3.4}",
                }
            ],
            "next_step": "verify",
            "confidence": 0.9,
        }
        result = parse_adjustment_plan(json.dumps(plan))
        self.assertIsNotNone(result)
        self.assertIn("{hue_error", result.adjustments[0]["reason"])

    def test_invalid_json_returns_none(self):
        """Non-JSON text returns None."""
        self.assertIsNone(parse_adjustment_plan("not json at all"))

    def test_valid_json_missing_adjustments_returns_none(self):
        """JSON without adjustments key returns None."""
        bad = json.dumps({"next_step": "verify", "confidence": 1.0})
        self.assertIsNone(parse_adjustment_plan(bad))

    def test_empty_adjustments_list_is_valid(self):
        """Empty adjustments array is a valid plan."""
        empty = json.dumps(
            {"adjustments": [], "next_step": "rerun_grayscale", "confidence": 0.0}
        )
        result = parse_adjustment_plan(empty)
        self.assertIsNotNone(result)
        self.assertEqual(result.adjustments, [])


# ── pytest-style parametrized tests ────────────────────────────────────────────

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("no json here", "no json here"),
        ("{bad json", "{bad json"),
        ("[]", "[]"),
    ],
)
def test_extract_json_invalid_inputs(raw, expected):
    """_extract_json never returns None; invalid inputs return stripped text."""
    result = _extract_json(raw)
    assert result == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no json here",
        "{bad json",
    ],
)
def test_extract_json_invalid_inputs_fail_json_loads(raw):
    """Invalid inputs should fail json.loads after extraction."""
    import json

    result = _extract_json(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_extract_json_array_returns_array_not_dict():
    """'[]' returns '[]' which parses as a list, not a dict."""
    import json

    result = _extract_json("[]")
    assert result == "[]"
    parsed = json.loads(result)
    assert isinstance(parsed, list)


if __name__ == "__main__":
    unittest.main()
