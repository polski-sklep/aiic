"""Settings resolution.

CONTRACTS 3.5: env var names are fixed by ``config.py::Settings`` and nothing
may read os.environ directly. These tests check the resolution rules and the
two defaults that carry a credential shape.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from app.config import Settings, get_settings


class SettingsResolutionTest(unittest.TestCase):
    def build(self, **env: str) -> Settings:
        """A Settings built from an explicit environment, ignoring any .env file."""
        with mock.patch.dict(os.environ, env, clear=True):
            return Settings(_env_file=None)

    def test_env_var_names_are_the_uppercased_field_names(self):
        settings = self.build(ANTHROPIC_API_KEY="sk-test", LOG_LEVEL="DEBUG", SONNET_MODEL="claude-x")
        self.assertEqual(settings.anthropic_api_key, "sk-test")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.sonnet_model, "claude-x")

    def test_field_names_are_case_insensitive_in_the_environment(self):
        self.assertEqual(self.build(anthropic_api_key="sk-lower").anthropic_api_key, "sk-lower")

    def test_unknown_env_vars_are_ignored_not_fatal(self):
        """model_config extra=ignore. A typo'd variable is silently dropped --
        deliberate, but it means NOTIONN_API_KEY fails as a missing key at call
        time rather than at startup."""
        settings = self.build(TOTALLY_UNKNOWN_SETTING="x")
        self.assertFalse(hasattr(settings, "totally_unknown_setting"))

    def test_model_tier_defaults_match_the_contracted_ids(self):
        """Handoff 9.5: dead model strings 404'd every call. Tiers resolve from
        settings, so these defaults are load-bearing."""
        settings = self.build()
        self.assertTrue(settings.sonnet_model.startswith("claude-"))
        self.assertTrue(settings.opus_model.startswith("claude-"))
        self.assertTrue(settings.haiku_model.startswith("claude-"))

    def test_get_settings_is_cached(self):
        """A documented consequence: editing .env needs a process restart
        (handoff 9.3, --force-recreate)."""
        self.assertIs(get_settings(), get_settings())

    def test_QA_036_database_url_default_must_not_embed_a_password(self):
        """QA-036 (MED): the default carries ``committee:committee_dev_pw``.

        Commit c62379c removed the ``:-default`` password fallback from
        docker-compose.yml and 98390a6 removed the hardcoded jwt_secret default,
        but this one survived both passes. It is the same defect class: a
        credential-shaped literal that makes a misconfigured deployment start
        successfully against the wrong database instead of failing loudly.

        Handoff 14.2 -- testing for the presence of a fix is not testing for the
        absence of the problem. Two commits say the hardcoded credentials were
        removed; one is still here.
        """
        settings = self.build()
        self.assertNotIn("committee_dev_pw", settings.database_url)

    @unittest.expectedFailure
    def test_QA_037_jwt_secret_must_not_default_to_empty(self):
        """QA-037 (MED): 98390a6 removed the hardcoded value but left "".

        An empty signing secret is not safer than a known one -- it is the same
        failure with no error message. Nothing refuses to start, so a deployment
        that forgets JWT_SECRET signs tokens with an empty key.
        """
        with self.assertRaises(Exception):
            self.build()


class ConfigDisciplineTest(unittest.TestCase):
    """CONTRACTS 3.5: never read os.environ directly for config."""

    def test_no_module_reads_os_environ_for_configuration(self):
        import pathlib
        import re

        app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders = []
        pattern = re.compile(r"os\.environ|os\.getenv")
        for path in app_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line) and not line.strip().startswith("#"):
                    offenders.append(f"{path.relative_to(app_root)}:{lineno}: {line.strip()}")

        self.assertEqual(offenders, [], "config must be read through get_settings()")


if __name__ == "__main__":
    unittest.main()
