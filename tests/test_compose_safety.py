from __future__ import annotations

from pathlib import Path

import pytest

from reprocase.environments.safety import (
    SafetyViolation,
    check_compose_safety,
    require_safe_compose,
)
from reprocase.errors import EnvironmentDriverError, FailureCode


def _config(service: dict[str, object]) -> dict[str, object]:
    return {"services": {"api": service}}


def _rules(violations: list[SafetyViolation]) -> set[str]:
    return {violation.rule for violation in violations}


def test_accepts_minimal_service() -> None:
    violations = check_compose_safety(
        _config({"image": "app:dev", "ports": ["127.0.0.1:18001:8000"]})
    )
    assert violations == []


def test_rejects_privileged() -> None:
    violations = check_compose_safety(_config({"privileged": True}))
    assert _rules(violations) == {"privileged"}


def test_rejects_host_namespaces() -> None:
    violations = check_compose_safety(
        _config({"network_mode": "host", "pid": "host", "ipc": "host"})
    )
    assert _rules(violations) == {"host-namespace"}
    assert len(violations) == 3


def test_rejects_docker_socket_mounts() -> None:
    violations = check_compose_safety(
        _config({"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]})
    )
    assert _rules(violations) == {"docker-socket"}
    long_form = check_compose_safety(
        _config(
            {
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/run/docker.sock",
                        "target": "/docker.sock",
                    }
                ]
            }
        )
    )
    assert _rules(long_form) == {"docker-socket"}


def test_rejects_host_root_and_home_mounts(tmp_path: Path) -> None:
    violations = check_compose_safety(
        _config({"volumes": ["/:/host", "~/:/home", f"{tmp_path}:/ws"]}),
        workspace_root=tmp_path,
    )
    assert _rules(violations) == {"host-root-mount", "home-mount", "workspace-mount"}


def test_allows_named_volumes() -> None:
    violations = check_compose_safety(
        _config({"volumes": [{"type": "volume", "source": "api-data", "target": "/data"}]})
    )
    assert violations == []


def test_rejects_added_capabilities() -> None:
    violations = check_compose_safety(_config({"cap_add": ["SYS_ADMIN"]}))
    assert _rules(violations) == {"cap-add"}


def test_rejects_remote_includes() -> None:
    raw = {"include": ["https://example.com/compose.yaml", "./local.yaml"]}
    violations = check_compose_safety(_config({"image": "app:dev"}), raw_compose=raw)
    assert _rules(violations) == {"remote-include"}
    local_only = check_compose_safety(
        _config({"image": "app:dev"}), raw_compose={"include": ["./local.yaml"]}
    )
    assert local_only == []


def test_require_safe_compose_raises_stable_code() -> None:
    with pytest.raises(EnvironmentDriverError) as excinfo:
        require_safe_compose(_config({"privileged": True, "network_mode": "host"}))
    assert excinfo.value.code == FailureCode.ENV_COMPOSE_UNSAFE
    assert excinfo.value.attribution == "ENVIRONMENT_ERROR"
    assert len(excinfo.value.details["violations"]) == 2
