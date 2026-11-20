"""Safe error summaries do not expose credentials or invent causes."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class DiagnosticsTests(unittest.TestCase):
    def test_credentials_are_removed_from_headers_json_env_and_urls(self):
        import agent_diagnostics as d
        values = ['abc-secret', 'json-secret', 'env-secret', 'query-secret', 'pass-secret', 'custom-secret']
        text = 'Authorization: Bearer abc-secret\n{"api_key":"json-secret"}\nANTHROPIC_AUTH_TOKEN=env-secret\nhttps://user:pass-secret@host/v1?api_key=query-secret\ncustom-secret'
        safe = d.redact(text, secrets=['custom-secret'])
        for secret in values:
            self.assertNotIn(secret, safe)
        self.assertIn('[REDACTED]', safe)

    def test_environment_secret_values_redacted_without_showing_configuration(self):
        import agent_diagnostics as d
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'very-private-value'}):
            self.assertNotIn('very-private-value', d.redact('provider said very-private-value is invalid'))

    def test_long_credentials_at_boundaries_and_command_flags_do_not_leak(self):
        import agent_diagnostics as d
        self.assertNotIn('sensitive', d.redact('Authorization: Bearer ' + 'sensitive' * 20000))
        self.assertNotIn('argument-secret', d.redact('--api-key argument-secret --verbose'))

    def test_classes_have_actionable_messages_and_unknown_is_not_guessed(self):
        import agent_diagnostics as d
        for text, category in [('HTTP 401 unauthorized', 'auth'), ('rate_limit_error 429', 'quota'),
                ('ECONNRESET', 'network'), ('model_not_found', 'model'),
                ('tool permission denied: Bash', 'permission'), ('task exceeded --timeout', 'timeout'),
                ('task cancelled', 'interrupted'), ('unexpected provider response', 'unknown')]:
            with self.subTest(category=category):
                result = d.diagnose(text)
                self.assertEqual(result['category'], category)
                self.assertTrue(result['message']); self.assertTrue(result['next_step'])
        result = d.diagnose('unknown error', state={'permission_denials': [{'tool_name': 'Bash'}]})
        self.assertEqual(result['category'], 'permission')
        self.assertIn('Bash', result['detail'])

    def test_detail_is_bounded_and_redacted_before_truncating(self):
        import agent_diagnostics as d
        result = d.diagnose('x' * 10000 + '\nAuthorization: Bearer never-expose')
        self.assertLessEqual(len(result['detail']), 4000)
        self.assertNotIn('never-expose', json.dumps(result))

    def test_claude_provider_result_error_is_saved_as_safe_diagnostic(self):
        import run_claude
        from tests.test_claude import ClaudeFixture
        fixture = ClaudeFixture(); fixture.setUp()
        try:
            fixture.binary.write_text(fixture.binary.read_text().replace('"result":"hello"', '"result":"authentication_error Authorization: Bearer provider-secret"'))
            done, directory, state = fixture.run_task('error')
            diagnostic = (directory / 'stderr.log').read_text()
            self.assertIn('authentication_error', diagnostic)
            self.assertNotIn('provider-secret', diagnostic)
        finally:
            fixture.doCleanups()
