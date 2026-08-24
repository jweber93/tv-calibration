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

    def test_unbalanced_brace_inside_string_value(self):
        """#644: an unbalanced '}' inside a string value must not truncate
        the object early — a naive depth counter returns depth to 0 mid-object."""
        raw = (
            '{"adjustments": [{"menu": "CMS", "setting": "Sat", "from": 5, '
            '"to": 3, "scope": "local", "reason": "hold the } edge of the curve"}], '
            '"next_step": "verify", "confidence": 0.9}'
        )
        result = _extract_json(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed["next_step"], "verify")
        self.assertIn("}", parsed["adjustments"][0]["reason"])

    def test_unbalanced_open_brace_inside_string_value(self):
        """#644: an unbalanced '{' inside a string value must not run the
        naive depth counter past the object's real closing brace."""
        raw = (
            '{"adjustments": [], "next_step": "verify", '
            '"confidence": 0.9, "note": "unopened { brace"} trailing garbage'
        )
        result = _extract_json(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed["next_step"], "verify")


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

    def test_top_level_array_returns_none(self):
        """#643: a top-level JSON array must return None, not raise AttributeError."""
        top_level_array = json.dumps(
            [{"menu": "CMS", "setting": "Sat", "from": 5, "to": 3, "scope": "local"}]
        )
        self.assertIsNone(parse_adjustment_plan(top_level_array))

    def test_top_level_scalar_returns_none(self):
        """#643: a top-level JSON scalar must return None, not raise AttributeError."""
        self.assertIsNone(parse_adjustment_plan(json.dumps("just a string")))
        self.assertIsNone(parse_adjustment_plan(json.dumps(42)))

    # ── per-adjustment field validation (#315) ─────────────────────────────

    def _make_adj(self, **overrides):
        base = {
            "menu": "White Balance",
            "setting": "B-Gain",
            "from": 0,
            "to": -3,
            "scope": "global",
            "reason": "Blue push above D65 locus.",
        }
        base.update(overrides)
        return base

    def _plan_with_adjustments(self, adjustments):
        return json.dumps(
            {
                "adjustments": adjustments,
                "next_step": "rerun_grayscale",
                "confidence": 0.85,
            }
        )

    def test_adjustment_missing_menu_returns_none(self):
        """Adjustment without 'menu' field is rejected."""
        adj = self._make_adj()
        del adj["menu"]
        payload = self._plan_with_adjustments([adj])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_adjustment_missing_setting_returns_none(self):
        """Adjustment without 'setting' field is rejected."""
        adj = self._make_adj()
        del adj["setting"]
        payload = self._plan_with_adjustments([adj])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_adjustment_missing_to_returns_none(self):
        """Adjustment without 'to' field is rejected."""
        adj = self._make_adj()
        del adj["to"]
        payload = self._plan_with_adjustments([adj])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_adjustment_missing_scope_returns_none(self):
        """Adjustment without 'scope' field is rejected."""
        adj = self._make_adj()
        del adj["scope"]
        payload = self._plan_with_adjustments([adj])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_adjustment_with_optional_fields_omitted_is_valid(self):
        """Adjustment missing optional fields 'from' and 'reason' is still valid."""
        adj = self._make_adj()
        del adj["from"]
        del adj["reason"]
        payload = self._plan_with_adjustments([adj])
        result = parse_adjustment_plan(payload)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.adjustments), 1)

    def test_adjustment_with_extra_fields_is_valid(self):
        """Adjustment with extra fields beyond required is still valid."""
        adj = self._make_adj()
        adj["colour"] = "Red"
        adj["priority"] = "high"
        payload = self._plan_with_adjustments([adj])
        result = parse_adjustment_plan(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result.adjustments[0]["colour"], "Red")

    def test_adjustment_not_a_dict_returns_none(self):
        """Non-dict adjustment item is rejected."""
        payload = self._plan_with_adjustments(["not_a_dict"])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_mixed_valid_and_invalid_adjustments_returns_none(self):
        """Plan with one valid and one invalid adjustment is rejected."""
        good = self._make_adj()
        bad = {"menu": "Color", "setting": "Red Gain"}  # missing 'to' and 'scope'
        payload = self._plan_with_adjustments([good, bad])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_null_adjustment_in_list_returns_none(self):
        """Adjustment list containing None is rejected."""
        payload = self._plan_with_adjustments([None])
        self.assertIsNone(parse_adjustment_plan(payload))

    def test_adjustment_null_required_field_returns_none(self):
        """Adjustment with null value for a required field is rejected."""
        adj = self._make_adj(to=None)
        adj["to"] = None
        payload = self._plan_with_adjustments([adj])
        self.assertIsNone(parse_adjustment_plan(payload))


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
