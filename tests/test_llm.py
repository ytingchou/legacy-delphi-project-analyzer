from __future__ import annotations

import json
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from legacy_delphi_project_analyzer.llm import run_llm_artifact, validate_openai_compatible_provider
from legacy_delphi_project_analyzer.pipeline import run_analysis


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeRawHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self) -> "_FakeRawHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class LlmIntegrationTests(unittest.TestCase):
    def test_validate_provider_reports_models_and_completion(self) -> None:
        recorded_urls: list[str] = []

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            recorded_urls.append(request.full_url)
            if request.full_url.endswith("/models"):
                return _FakeHttpResponse(
                    {
                        "object": "list",
                        "data": [{"id": "qwen3-test"}, {"id": "qwen3-fallback"}],
                    }
                )
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"ok": True, "provider": "validated"}),
                            }
                        }
                    ]
                }
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = validate_openai_compatible_provider(
                provider_base_url="http://provider.example",
                model="qwen3-test",
                api_key="secret-token",
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["models_ok"])
        self.assertTrue(result["completion_ok"])
        self.assertEqual(result["selected_model"], "qwen3-test")
        self.assertIn("http://provider.example/v1/models", recorded_urls)
        self.assertIn("http://provider.example/v1/chat/completions", recorded_urls)

    def test_validate_provider_collects_debug_when_models_fail(self) -> None:
        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = validate_openai_compatible_provider(
                provider_base_url="http://provider.example",
                model="qwen3-test",
                perform_completion=False,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["models_ok"])
        self.assertIn("Could not reach provider endpoint", result["debug"][0])

    def test_run_llm_uses_prompt_pack_and_writes_feedback_template(self) -> None:
        recorded_request: dict = {}

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            recorded_request["url"] = request.full_url
            recorded_request["headers"] = dict(request.header_items())
            recorded_request["body"] = json.loads(request.data.decode("utf-8"))
            recorded_request["timeout"] = timeout
            return _FakeHttpResponse(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"result": "ok", "echo_model": "qwen3-test"}),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 111,
                        "completion_tokens": 22,
                        "total_tokens": 133,
                    },
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = run_llm_artifact(
                    analysis_dir=Path(output.output_dir),
                    prompt_name="OrderLookupClarify",
                    provider_base_url="http://provider.example",
                    model="qwen3-test",
                    api_key="secret-token",
                    output_token_limit=256,
                    token_limit=1200,
                )

            self.assertEqual(result.artifact_kind, "prompt-pack")
            self.assertEqual(result.artifact_name, "OrderLookupClarify")
            self.assertEqual(result.parsed_response["result"], "ok")
            self.assertTrue(Path(result.feedback_template_path or "").exists())
            self.assertTrue((Path(output.output_dir) / "llm-runs" / f"{result.run_id}.json").exists())
            self.assertEqual(recorded_request["url"], "http://provider.example/v1/chat/completions")
            self.assertEqual(recorded_request["body"]["model"], "qwen3-test")
            self.assertEqual(recorded_request["body"]["max_tokens"], 256)
            self.assertEqual(recorded_request["headers"]["Authorization"], "Bearer secret-token")

    def test_run_llm_respects_context_token_limit(self) -> None:
        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"result": "ok"}),
                            }
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            analysis_dir = Path(tmpdir) / "analysis"
            prompt_dir = analysis_dir / "prompt-pack"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            context_one = analysis_dir / "context-one.md"
            context_two = analysis_dir / "context-two.md"
            context_one.write_text("A\n" * 200, encoding="utf-8")
            context_two.write_text("B\n" * 200, encoding="utf-8")
            (prompt_dir / "manualprompt.json").write_text(
                json.dumps(
                    {
                        "name": "ManualPrompt",
                        "goal": "resolve_search_path",
                        "target_model": "qwen3-128k",
                        "context_budget_tokens": 1000,
                        "context_paths": [context_one.as_posix(), context_two.as_posix()],
                        "primary_prompt": "Return JSON with keys result.",
                        "expected_response_schema": {"result": "string"},
                    }
                ),
                encoding="utf-8",
            )

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = run_llm_artifact(
                    analysis_dir=analysis_dir,
                    prompt_name="ManualPrompt",
                    provider_base_url="http://provider.example/v1",
                    model="manual-model",
                    token_limit=120,
                    output_token_limit=64,
                )

            self.assertEqual(result.included_context_paths, [context_one.as_posix()])
            self.assertIn(context_two.as_posix(), result.skipped_context_paths)
            self.assertLessEqual(result.input_token_limit, 120)

    def test_run_llm_accepts_plain_text_provider_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            with patch("urllib.request.urlopen", return_value=_FakeRawHttpResponse('{"result":"ok-from-plain-text"}')):
                result = run_llm_artifact(
                    analysis_dir=Path(output.output_dir),
                    prompt_name="OrderLookupClarify",
                    provider_base_url="http://provider.example",
                    model="qwen3-test",
                    output_token_limit=128,
                    token_limit=800,
                )
            self.assertEqual(result.parsed_response["result"], "ok-from-plain-text")

    def test_run_llm_accepts_sse_provider_response(self) -> None:
        sse_payload = "\n".join(
            [
                'data: {"choices":[{"delta":{"content":"{\\"result\\": "}}]}',
                'data: {"choices":[{"delta":{"content":"\\"ok-from-sse\\"}"}}]}',
                "data: [DONE]",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            with patch("urllib.request.urlopen", return_value=_FakeRawHttpResponse(sse_payload)):
                result = run_llm_artifact(
                    analysis_dir=Path(output.output_dir),
                    prompt_name="OrderLookupClarify",
                    provider_base_url="http://provider.example",
                    model="qwen3-test",
                    output_token_limit=128,
                    token_limit=800,
                )
            self.assertEqual(result.parsed_response["result"], "ok-from-sse")


if __name__ == "__main__":
    unittest.main()
