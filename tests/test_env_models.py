from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from reprocase.case.loader import load_case
from reprocase.case.models import ComposeEnvironmentDefinition, EnvironmentDefinition
from reprocase.environments.compose import make_project_name
from reprocase.errors import CaseValidationError, FailureCode
from reprocase.runner.orchestrator import run_case

ROOT = Path(__file__).resolve().parents[1]
ADAPTER: TypeAdapter[EnvironmentDefinition] = TypeAdapter(EnvironmentDefinition)


def test_compose_environment_definition_parses() -> None:
    definition = ADAPTER.validate_python(
        {
            "api_version": "reprocase.dev/environment/v0",
            "driver": "docker_compose",
            "compose": {"port_variables": {"API_PORT": 8000}},
            "restore": {
                "service": "api",
                "command": ["python", "-m", "app.reset", "/fixtures/refund.json"],
            },
            "healthcheck": {"url": "http://127.0.0.1:${API_PORT}/health"},
            "state": {
                "capture": {"service": "api", "command": ["python", "-m", "app.export_state"]}
            },
        }
    )
    assert isinstance(definition, ComposeEnvironmentDefinition)
    assert definition.compose.project_prefix == "reprocase"
    assert definition.restore.strategy == "command"


def test_compose_definition_requires_lifecycle_sections() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "api_version": "reprocase.dev/environment/v0",
                "driver": "docker_compose",
                "compose": {},
            }
        )


def test_fake_definition_still_parses() -> None:
    definition = ADAPTER.validate_python(
        {
            "api_version": "reprocase.dev/environment/v0",
            "driver": "fake",
            "config": {"healthy": False},
        }
    )
    assert definition.driver == "fake"


def test_unknown_driver_rejected() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {"api_version": "reprocase.dev/environment/v0", "driver": "kubernetes"}
        )


def test_loader_resolves_compose_file_and_extends_digest(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "environment.yaml").write_text(
        """\
api_version: reprocase.dev/environment/v0
driver: docker_compose
compose:
  file: compose.yaml
restore:
  service: api
  command: ["true"]
healthcheck:
  url: http://127.0.0.1/health
state:
  capture:
    service: api
    command: ["true"]
""",
        encoding="utf-8",
    )
    (tmp_path / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: compose.example.case
  version: 0.1.0
  title: Compose case
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: docker_compose
  definition: environment.yaml
  compose_file: compose.yaml
graders:
  - id: g1
    type: fake
    explanation: ok
""",
        encoding="utf-8",
    )
    loaded = load_case(tmp_path / "case.yaml")
    assert isinstance(loaded.environment, ComposeEnvironmentDefinition)
    assert loaded.compose_path == (tmp_path / "compose.yaml").resolve()

    (tmp_path / "compose.yaml").write_text("services: {changed: {}}\n", encoding="utf-8")
    assert load_case(tmp_path / "case.yaml").digest != loaded.digest

    with pytest.raises(CaseValidationError) as excinfo:
        run_case(tmp_path / "case.yaml", tmp_path / "runs")
    assert excinfo.value.code == FailureCode.CASE_UNSUPPORTED_DRIVER


def test_loader_rejects_driver_mismatch(tmp_path: Path) -> None:
    (tmp_path / "environment.yaml").write_text(
        "api_version: reprocase.dev/environment/v0\ndriver: fake\n",
        encoding="utf-8",
    )
    (tmp_path / "case.yaml").write_text(
        """\
api_version: reprocase.dev/v0
kind: EvalCase
metadata:
  id: mismatch.example.case
  version: 0.1.0
  title: Mismatch
task:
  input: {}
agent:
  adapter: fake
environment:
  driver: docker_compose
  definition: environment.yaml
graders:
  - id: g1
    type: fake
    explanation: ok
""",
        encoding="utf-8",
    )
    with pytest.raises(CaseValidationError):
        load_case(tmp_path / "case.yaml")


def test_project_name_is_compose_safe() -> None:
    name = make_project_name("reprocase", "Support.Refund.Standard", "abc123def4")
    assert name == "reprocase_support_refund_standard_abc123def4"
    assert make_project_name("reprocase", "!!!", "abc123") == "reprocase_case_abc123"
