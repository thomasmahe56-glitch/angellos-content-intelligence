import pytest
from fastapi import HTTPException

from server import _require_agent_token


class Request:
    def __init__(self, header=""):
        self.headers = {"authorization": header}


def test_agent_token_rejects_missing_or_invalid_token(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "secret")
    with pytest.raises(HTTPException) as error:
        _require_agent_token(Request("Bearer wrong"))
    assert error.value.status_code == 401


def test_agent_token_accepts_matching_bearer_token(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "secret")
    _require_agent_token(Request("Bearer secret"))
