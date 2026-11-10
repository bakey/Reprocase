"""M8 acceptance: the LLM agent end-to-end against a scripted OpenAI-compatible
server (host-side) with the real Acme World container (docker).

The fake server only decides the model's moves; every tool call executes the
real in-container API, so verdicts grade genuine state changes.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import requires_docker, residual_resources

from reprocase.errors import FailureCode
from reprocase.results.models import RunStatus, Verdict
from reprocase.runner.orchestrator import run_case

ROOT = Path(__file__).resolve().parents[1]
ACME_DIR = ROOT / "examples" / "acme-world"
SMOKE_LLM = ACME_DIR / "refund-smoke-llm.yaml"

pytestmark = [requires_docker, pytest.mark.docker]


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeOpenAI/1.0"

    def do_POST(self) -> None:
        server: _FakeServer = self.server  # type: ignore[assignment]
        self.rfile.read(int(self.headers["Content-Length"]))
        response = server.responses.pop(0)
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _FakeServer(ThreadingHTTPServer):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.responses = list(responses)


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def _final(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 20},
    }


@pytest.fixture()
def fake_openai():
    server = _FakeServer([])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _events(output: Path, run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (output / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_llm_smoke_case_passes_end_to_end(
    tmp_path: Path, fake_openai: _FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_openai.responses = [
        _tool_call("refund.quote", {"order_id": "ORD-1001"}, "c1"),
        _tool_call("refund.create", {"order_id": "ORD-1001", "amount": 49.9}, "c2"),
        _tool_call("email.send", {"customer_id": "CUST-1001", "template": "refund_complete"}, "c3"),
        _final("退款已完成并已通知客户。"),
    ]
    monkeypatch.setenv(
        "REPROCASE_LLM_BASE_URL", f"http://127.0.0.1:{fake_openai.server_address[1]}/v1"
    )
    monkeypatch.setenv("REPROCASE_LLM_API_KEY", "sk-e2e-test-key-123456")
    monkeypatch.setenv("REPROCASE_LLM_MODEL", "fake-chat")

    output = tmp_path / "runs"
    result = run_case(SMOKE_LLM, output)

    assert result.verdict == Verdict.PASS
    assert result.status == RunStatus.COMPLETED
    # usage comes from the scripted API responses, accumulated over 4 calls
    assert result.usage is not None
    assert result.usage.model_calls == 4
    assert result.usage.input_tokens == 120 + 120 + 120 + 80
    assert result.usage.output_tokens == 30 + 30 + 30 + 20
    # observed model identity lands in the run result (manifest predates the run)
    assert result.agent is not None
    assert result.agent.model == "fake-chat"
    manifest = json.loads((output / result.run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["agent"]["model"] is None
    assert residual_resources() == []


def test_secret_scrubbing_survives_model_echoing_the_key(
    tmp_path: Path, fake_openai: _FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-echo-me-987654321"
    fake_openai.responses = [
        _final(f"my key is {secret} and I will keep saying {secret}"),
    ]
    monkeypatch.setenv(
        "REPROCASE_LLM_BASE_URL", f"http://127.0.0.1:{fake_openai.server_address[1]}/v1"
    )
    monkeypatch.setenv("REPROCASE_LLM_API_KEY", secret)
    monkeypatch.setenv("REPROCASE_LLM_MODEL", "fake-chat")

    output = tmp_path / "runs"
    result = run_case(SMOKE_LLM, output)

    # the run fails on graders (nothing happened) but must be valid evidence
    assert result.verdict == Verdict.FAIL
    events_text = (output / result.run_id / "events.jsonl").read_text(encoding="utf-8")
    result_text = (output / result.run_id / "result.json").read_text(encoding="utf-8")
    report_text = (output / result.run_id / "report.html").read_text(encoding="utf-8")
    for name, blob in (("events", events_text), ("result", result_text), ("report", report_text)):
        assert secret not in blob, f"key leaked into {name}"
        assert "[REDACTED]" in blob, f"{name} should show the scrub marker"
    assert residual_resources() == []


def test_budget_exhaustion_is_primary_agent_failure(
    tmp_path: Path, fake_openai: _FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _tool_call("refund.quote", {"order_id": "ORD-1001"}, "loop")
    fake_openai.responses = [loop] * 10
    monkeypatch.setenv(
        "REPROCASE_LLM_BASE_URL", f"http://127.0.0.1:{fake_openai.server_address[1]}/v1"
    )
    monkeypatch.setenv("REPROCASE_LLM_API_KEY", "sk-budget-test-123")
    monkeypatch.setenv("REPROCASE_LLM_MODEL", "fake-chat")

    import shutil

    import yaml

    # case references must stay inside the case root: copy the assets the
    # loader resolves relative to the case (same pattern as the grader matrix)
    environment = yaml.safe_load((ACME_DIR / "environment.yaml").read_text(encoding="utf-8"))
    environment["compose"]["file"] = str(ACME_DIR / "compose.yaml")
    (tmp_path / "environment.yaml").write_text(yaml.safe_dump(environment), encoding="utf-8")
    shutil.copytree(ACME_DIR / "agents", tmp_path / "agents")
    shutil.copytree(ACME_DIR / "tools", tmp_path / "tools")

    case = _case_document()
    case["budgets"]["max_tool_calls"] = 3
    case_path = tmp_path / "budget-case.yaml"
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")

    output = tmp_path / "runs"
    result = run_case(case_path, output)

    assert result.verdict == Verdict.FAIL
    assert result.primary_failure is not None
    assert result.primary_failure.code == FailureCode.AGENT_BUDGET_EXCEEDED
    assert residual_resources() == []


def _case_document() -> dict[str, Any]:
    import yaml

    document = yaml.safe_load(SMOKE_LLM.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document
