"""Unit coverage for ${FIXTURE} restore parameterization (M6-04)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from reprocase.case.loader import load_case, resolve_restore_fixture
from reprocase.case.models import ComposeEnvironmentDefinition, RestoreSpec
from reprocase.environments.compose import substitute_fixture
from reprocase.errors import CaseValidationError, FailureCode


def _compose_definition(
    command: list[str], fixture_default: str | None = None
) -> ComposeEnvironmentDefinition:
    return ComposeEnvironmentDefinition.model_validate(
        {
            "api_version": "reprocase.dev/environment/v0",
            "driver": "docker_compose",
            "compose": {"file": "compose.yaml", "port_variables": {"API_PORT": 8000}},
            "restore": {
                "service": "api",
                "command": command,
                **({"fixture_default": fixture_default} if fixture_default else {}),
            },
            "healthcheck": {"url": "http://127.0.0.1:${API_PORT}/health"},
            "state": {"capture": {"service": "api", "command": ["python", "-m", "app.export"]}},
        }
    )


def _write_case(root: Path, fixture: str | None) -> Path:
    document = {
        "api_version": "reprocase.dev/v0",
        "kind": "EvalCase",
        "metadata": {"id": "fixture.case", "version": "0.1.0", "title": "fixture"},
        "task": {"input": {"message": "hi"}},
        "agent": {"adapter": "fake"},
        "environment": {
            "driver": "docker_compose",
            "definition": "environment.yaml",
            **({"fixture": fixture} if fixture is not None else {}),
        },
        "graders": [{"id": "g", "type": "fake", "explanation": "x"}],
    }
    path = root / "case.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path: Path) -> Path:
    (tmp_path / "compose.yaml").write_text("services: {api: {image: t}}\n", encoding="utf-8")
    return tmp_path


PLACEHOLDER_COMMAND = ["python", "-m", "app.reset", "${FIXTURE}"]
PLAIN_COMMAND = ["python", "-m", "app.reset", "/fixtures/default.json"]


class TestResolveRestoreFixture:
    def test_placeholder_without_any_fixture_is_missing_reference(self) -> None:
        definition = _compose_definition(PLACEHOLDER_COMMAND)
        with pytest.raises(CaseValidationError) as excinfo:
            resolve_restore_fixture(case_fixture=None, environment=definition)
        assert excinfo.value.code == FailureCode.CASE_REFERENCE_MISSING

    def test_case_fixture_replaces_placeholder(self) -> None:
        definition = _compose_definition(PLACEHOLDER_COMMAND)
        assert (
            resolve_restore_fixture(case_fixture="/fixtures/a.json", environment=definition)
            == "/fixtures/a.json"
        )

    def test_default_fixture_used_when_case_has_none(self) -> None:
        definition = _compose_definition(PLACEHOLDER_COMMAND, "/fixtures/d.json")
        assert resolve_restore_fixture(case_fixture=None, environment=definition) == (
            "/fixtures/d.json"
        )

    def test_case_fixture_overrides_default(self) -> None:
        definition = _compose_definition(PLACEHOLDER_COMMAND, "/fixtures/d.json")
        assert resolve_restore_fixture(case_fixture="/fixtures/a.json", environment=definition) == (
            "/fixtures/a.json"
        )

    def test_fixture_without_placeholder_is_schema_error(self) -> None:
        definition = _compose_definition(PLAIN_COMMAND)
        with pytest.raises(CaseValidationError) as excinfo:
            resolve_restore_fixture(case_fixture="/fixtures/a.json", environment=definition)
        assert excinfo.value.code == FailureCode.CASE_SCHEMA_INVALID


class TestLoadCaseFixture:
    def test_compose_case_resolves_default_fixture(self, world: Path) -> None:
        (world / "environment.yaml").write_text(
            yaml.safe_dump(
                _compose_definition(PLACEHOLDER_COMMAND, "/fixtures/d.json").model_dump(mode="json")
            ),
            encoding="utf-8",
        )
        loaded = load_case(_write_case(world, None))
        assert loaded.fixture == "/fixtures/d.json"

    def test_case_fixture_flows_into_loaded_case(self, world: Path) -> None:
        (world / "environment.yaml").write_text(
            yaml.safe_dump(
                _compose_definition(PLACEHOLDER_COMMAND, "/fixtures/d.json").model_dump(mode="json")
            ),
            encoding="utf-8",
        )
        loaded = load_case(_write_case(world, "/fixtures/other.json"))
        assert loaded.fixture == "/fixtures/other.json"

    def test_fixture_changes_the_case_digest(self, world: Path) -> None:
        (world / "environment.yaml").write_text(
            yaml.safe_dump(
                _compose_definition(PLACEHOLDER_COMMAND, "/fixtures/d.json").model_dump(mode="json")
            ),
            encoding="utf-8",
        )
        first = load_case(_write_case(world, "/fixtures/a.json"))
        second = load_case(_write_case(world, "/fixtures/b.json"))
        assert first.digest != second.digest


class TestSubstituteFixture:
    def test_none_passes_through(self) -> None:
        assert substitute_fixture(list(PLACEHOLDER_COMMAND), None) == PLACEHOLDER_COMMAND

    def test_lone_placeholder_element_replaced(self) -> None:
        assert substitute_fixture(list(PLACEHOLDER_COMMAND), "/fixtures/x.json") == [
            "python",
            "-m",
            "app.reset",
            "/fixtures/x.json",
        ]

    def test_embedded_placeholder_replaced_in_place(self) -> None:
        command = ["--set", "fixture=${FIXTURE}!extra"]
        assert substitute_fixture(command, "/x") == ["--set", "fixture=/x!extra"]


class TestRestoreSpecModel:
    def test_references_fixture_detection(self) -> None:
        assert RestoreSpec(service="api", command=tuple(PLACEHOLDER_COMMAND)).references_fixture
        assert not RestoreSpec(service="api", command=tuple(PLAIN_COMMAND)).references_fixture

    def test_fixture_default_accepted(self) -> None:
        spec = RestoreSpec(
            service="api",
            command=tuple(PLACEHOLDER_COMMAND),
            fixture_default="/fixtures/d.json",
        )
        assert spec.fixture_default == "/fixtures/d.json"
