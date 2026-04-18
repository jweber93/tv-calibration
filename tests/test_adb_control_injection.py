"""Tests for shell-injection defence inside _cms_tool (Issue #151).

Every current caller validates inputs upstream (enum lookups for CMS_CHANNELS,
range checks on value), but _cms_tool must also reject untrusted strings at
the adb-shell layer so that a future caller cannot bypass validation.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import calibrator.adb_control as adb_mod


def _completed(returncode=0, stdout="", stderr=""):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── Direct _cms_tool injection tests ──────────────────────────────────────────

class TestCmsToolArgValidation:
    """Verify _cms_tool rejects shell-metacharacter args before reaching subprocess."""

    @pytest.mark.parametrize(
        "malicious",
        [
            "0; rm -rf /sdcard/*",   # semicolon + command chaining
            "0`whoami`",              # backtick substitution
            "0|cat /etc/passwd",      # pipe
            "0$(reboot)",             # $() command substitution
            "0 && echo pwned",        # && chaining with space
            "0; echo pwned",          # semicolon with space
            "0\n echo pwned",         # newline injection
            "\nset_hue",              # leading newline
            "set_hue\n",              # trailing newline
            "set;hue",                # semicolon inside action name
            "get_all; rm -rf /",      # semicolon after valid action
            "set_hue #injected",      # comment-style injection
            "set\"hue",               # escaped quote
        ],
    )
    def test_rejects_shell_metacharacters(self, malicious):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Refusing to pass unsafe arg"):
                adb_mod._cms_tool(malicious)
        # subprocess.run must NOT have been called
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "safe",
        [
            "set_hue",
            "set_saturation",
            "set_brightness",
            "get_all",
            "get_hue",
            "set_picture_mode",
            "set_brightness_main",
            "1",
            "0",
            "-10",
            "10",
            "Red",
            "Green",
            "Blue",
            "Yellow",
            "Cyan",
            "Magenta",
            "test_123",
            "my-action",
            "A1B2C3",
        ],
    )
    def test_accepts_safe_args(self, safe):
        """Safe args (alphanumeric, underscore, hyphen) pass through to adb."""
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=0 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod._cms_tool(safe)
        assert result.returncode == 0
        # Verify adb was actually called with the arg in the shell command
        shell_cmd = " ".join(run.call_args[0][0])
        assert safe in shell_cmd

    def test_empty_string_rejected(self):
        """Empty string is not matched by the whitelist regex."""
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Refusing to pass unsafe arg"):
                adb_mod._cms_tool("")
        mock_run.assert_not_called()

    def test_unicode_rejected(self):
        """Unicode characters are rejected."""
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Refusing to pass unsafe arg"):
                adb_mod._cms_tool("set_hue\u200b")  # zero-width space
        mock_run.assert_not_called()

    def test_multiple_args_all_validated(self):
        """All args are checked, not just the first."""
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Refusing to pass unsafe arg"):
                adb_mod._cms_tool("set_hue", "1", "0; rm -rf /")
        mock_run.assert_not_called()

    def test_valid_multi_arg_call_succeeds(self):
        """Normal multi-arg calls (action, channel_id, value) all pass."""
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=3 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod._cms_tool("set_hue", "1", "3")
        assert result.returncode == 0
        shell_cmd = " ".join(run.call_args[0][0])
        assert "set_hue" in shell_cmd
        assert "1" in shell_cmd
        assert "3" in shell_cmd


# ── Integration: public APIs still work through validation ────────────────────

class TestPublicApisThroughValidation:
    """Ensure existing callers are not broken by the new validation layer."""

    def test_set_cms_value_still_works(self):
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=3 ret=0")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.set_cms_value("Red", "Hue", 3)
        assert result["ok"] is True

    def test_get_cms_value_still_works(self):
        mock = _completed(returncode=0, stdout="VALUE 5\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_cms_value("Red", "Hue")
        assert result["ok"] is True
        assert result["value"] == 5

    def test_get_all_cms_values_still_works(self):
        output = (
            "Red hue=5 sat=0 bri=0\n"
            "Green hue=0 sat=-3 bri=0\n"
            "Blue hue=0 sat=0 bri=2\n"
            "Yellow hue=0 sat=0 bri=0\n"
            "Cyan hue=0 sat=0 bri=0\n"
            "Magenta hue=0 sat=0 bri=0\n"
        )
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            result = adb_mod.get_all_cms_values()
        assert result["ok"] is True
        assert result["values"]["Red"]["hue"] == 5

    def test_reset_cms_still_works(self):
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=0 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod.reset_cms()
        assert result["ok"] is True
        # 6 channels x 3 controls = 18 calls
        assert run.call_count == 18

    def test_set_picture_control_still_works(self):
        mock = _completed(returncode=0, stdout="OK set_brightness_main val=50 ret=0")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.set_picture_control("Brightness", 50)
        assert result["ok"] is True

    def test_get_picture_control_still_works(self):
        mock = _completed(returncode=0, stdout="VALUE 42\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_picture_control("Brightness")
        assert result["ok"] is True
        assert result["value"] == 42
