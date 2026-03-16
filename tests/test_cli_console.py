from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from legacy_delphi_project_analyzer.cli import main


class CliConsoleTests(unittest.TestCase):
    def test_cli_formats_retry_plan_error_with_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["retry-plan", tmpdir, "missing-task"])
        output = stream.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("Error while running `retry-plan`", output)
        self.assertIn("Hint:", output)

    def test_cli_validate_provider_shows_progress_and_debug(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            with patch(
                "legacy_delphi_project_analyzer.cli.validate_openai_compatible_provider",
                return_value={
                    "provider_base_url": "http://provider.example",
                    "models_endpoint": "http://provider.example/v1/models",
                    "chat_endpoint": "http://provider.example/v1/chat/completions",
                    "auth_configured": False,
                    "models_ok": True,
                    "completion_ok": True,
                    "selected_model": "qwen3-test",
                    "listed_models": ["qwen3-test"],
                    "response_preview": '{"ok":true}',
                    "debug": ["models endpoint reachable"],
                    "ok": True,
                },
            ):
                code = main(
                    [
                        "validate-provider",
                        "--provider-base-url",
                        "http://provider.example",
                        "--verbose",
                    ]
                )
        output = stream.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[progress]", output)
        self.assertIn("Provider base URL", output)
        self.assertIn("Debug:", output)


if __name__ == "__main__":
    unittest.main()
