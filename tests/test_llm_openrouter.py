"""Tests for first-class OpenRouter support in LLMConfig (#272).

Covers:
  - LLMConfig.from_dict provider/header parsing + endpoint auto-fill
  - _build_request provider-specific header injection
  - probe_llm sending the same headers as a real call
"""

import unittest
from unittest.mock import patch

from calcore.llm import _build_request, probe_llm
from calcore.models import LLMConfig


def _headers(req) -> dict:
    """Return request headers lower-cased for case-insensitive assertions."""
    return {k.lower(): v for k, v in req.header_items()}


class LLMConfigProviderTests(unittest.TestCase):
    def test_defaults_have_empty_provider(self):
        cfg = LLMConfig()
        self.assertEqual(cfg.provider, "")
        self.assertEqual(cfg.http_referer, "")
        self.assertEqual(cfg.app_title, "tv-calibration")

    def test_openrouter_autofills_endpoint(self):
        cfg = LLMConfig(provider="openrouter", model="anthropic/claude-3-5-sonnet")
        self.assertEqual(cfg.endpoint, "https://openrouter.ai/api/v1")

    def test_explicit_endpoint_is_not_overridden(self):
        cfg = LLMConfig(provider="openrouter", endpoint="http://localhost:4000/v1")
        self.assertEqual(cfg.endpoint, "http://localhost:4000/v1")

    def test_provider_is_normalised(self):
        cfg = LLMConfig(provider="  OpenRouter ")
        self.assertEqual(cfg.provider, "openrouter")
        self.assertEqual(cfg.endpoint, "https://openrouter.ai/api/v1")

    def test_unknown_provider_does_not_autofill(self):
        cfg = LLMConfig(provider="litellm")
        self.assertEqual(cfg.endpoint, "")

    def test_litellm_provider_keeps_explicit_endpoint(self):
        cfg = LLMConfig(provider="litellm", endpoint="http://localhost:4000/v1")
        self.assertEqual(cfg.endpoint, "http://localhost:4000/v1")

    def test_from_dict_parses_provider_fields(self):
        cfg = LLMConfig.from_dict(
            {
                "provider": "openrouter",
                "model": "anthropic/claude-3-5-sonnet",
                "api_key": "sk-or-test",
                "http_referer": "https://github.com/jweber93/tv-calibration",
                "app_title": "my-app",
            }
        )
        self.assertEqual(cfg.provider, "openrouter")
        self.assertEqual(cfg.endpoint, "https://openrouter.ai/api/v1")
        self.assertEqual(cfg.http_referer, "https://github.com/jweber93/tv-calibration")
        self.assertEqual(cfg.app_title, "my-app")

    def test_from_dict_defaults_app_title(self):
        cfg = LLMConfig.from_dict({"provider": "openrouter"})
        self.assertEqual(cfg.app_title, "tv-calibration")


class BuildRequestTests(unittest.TestCase):
    URL = "https://openrouter.ai/api/v1/chat/completions"
    BODY = {"model": "m", "messages": []}

    def test_content_type_and_method(self):
        req = _build_request(self.URL, self.BODY, LLMConfig())
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(_headers(req)["content-type"], "application/json")
        self.assertEqual(req.full_url, self.URL)

    def test_bearer_auth_when_key_present(self):
        req = _build_request(self.URL, self.BODY, LLMConfig(api_key="sk-or-test"))
        self.assertEqual(_headers(req)["authorization"], "Bearer sk-or-test")

    def test_no_auth_header_without_key(self):
        req = _build_request(self.URL, self.BODY, LLMConfig())
        self.assertNotIn("authorization", _headers(req))

    def test_openrouter_injects_referer_and_title(self):
        cfg = LLMConfig(
            provider="openrouter",
            api_key="sk-or-test",
            http_referer="https://example.com",
            app_title="my-app",
        )
        h = _headers(_build_request(self.URL, self.BODY, cfg))
        self.assertEqual(h["http-referer"], "https://example.com")
        self.assertEqual(h["x-title"], "my-app")

    def test_openrouter_title_falls_back_when_blank(self):
        cfg = LLMConfig(provider="openrouter", app_title="")
        h = _headers(_build_request(self.URL, self.BODY, cfg))
        self.assertEqual(h["x-title"], "tv-calibration")

    def test_openrouter_omits_referer_when_blank(self):
        cfg = LLMConfig(provider="openrouter")
        h = _headers(_build_request(self.URL, self.BODY, cfg))
        self.assertNotIn("http-referer", h)
        self.assertIn("x-title", h)

    def test_generic_provider_has_no_openrouter_headers(self):
        cfg = LLMConfig(api_key="sk-test", http_referer="https://example.com")
        h = _headers(_build_request(self.URL, self.BODY, cfg))
        self.assertNotIn("http-referer", h)
        self.assertNotIn("x-title", h)


class ProbeLlmHeaderTests(unittest.TestCase):
    """probe_llm must send the same headers as a real call so it can't pass
    while real calls fail (e.g. a bad HTTP-Referer hitting a blocklist)."""

    def test_probe_builds_openrouter_headers(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            raise AssertionError("stop before network")

        with patch("calcore.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            probe_llm(
                {
                    "provider": "openrouter",
                    "model": "anthropic/claude-3-5-sonnet",
                    "api_key": "sk-or-test",
                    "http_referer": "https://example.com",
                    "app_title": "probe-app",
                }
            )

        h = _headers(captured["req"])
        self.assertEqual(captured["req"].full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(h["authorization"], "Bearer sk-or-test")
        self.assertEqual(h["http-referer"], "https://example.com")
        self.assertEqual(h["x-title"], "probe-app")

    def test_probe_requires_endpoint_and_model(self):
        ok, err = probe_llm({"provider": "litellm"})
        self.assertFalse(ok)
        self.assertIn("required", err)

    def test_probe_openrouter_autofills_endpoint_for_validation(self):
        # provider=openrouter + model but no endpoint should pass the
        # endpoint/model presence check (endpoint is auto-filled).
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            raise AssertionError("stop before network")

        with patch("calcore.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            probe_llm({"provider": "openrouter", "model": "m"})

        self.assertIn("req", captured)


if __name__ == "__main__":
    unittest.main()
