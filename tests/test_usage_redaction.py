from __future__ import annotations

import pytest
from pydantic import ValidationError

from reprocase.results.models import Usage
from reprocase.tools.redaction import REDACTED, redact


def test_usage_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        Usage(tool_calls=-1)
    with pytest.raises(ValidationError):
        Usage(model_calls=-1)
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)
    with pytest.raises(ValidationError):
        Usage(output_tokens=-1)
    with pytest.raises(ValidationError):
        Usage(cost_usd=-0.01)


def test_usage_accepts_zero_and_none() -> None:
    usage = Usage(tool_calls=0, model_calls=0, cost_usd=0.0)
    assert usage.input_tokens is None


def test_redact_recurses_tuples_and_nested_containers() -> None:
    value = {
        "items": (
            {"password": "pw", "ok": 1},
            [{"token": "tok"}, "plain"],
        ),
        "nested": {"Authorization": "Bearer x"},
    }
    redacted = redact(value)
    assert redacted == {
        "items": [
            {"password": REDACTED, "ok": 1},
            [{"token": REDACTED}, "plain"],
        ],
        "nested": {"Authorization": REDACTED},
    }
