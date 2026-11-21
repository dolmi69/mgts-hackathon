import json
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from app.services import llm


def test_gigachat_reuses_oauth_token_and_validates_quotes(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_api_key", "test-auth-key")
    monkeypatch.setattr(llm.settings, "gigachat_ca_bundle", "")
    monkeypatch.setattr(llm.settings, "gigachat_base_url", "https://api.giga.chat/v1")
    monkeypatch.setattr(llm.settings, "gigachat_scope", "GIGACHAT_API_PERS")
    monkeypatch.setattr(llm.settings, "gigachat_model", "GigaChat")
    oauth_requests = []
    chat_requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == llm.GigaChatProvider.oauth_url:
            assert request.headers["authorization"] == "Basic test-auth-key"
            assert UUID(request.headers["rquid"]).version == 4
            assert parse_qs(request.content.decode()) == {"scope": ["GIGACHAT_API_PERS"]}
            oauth_requests.append(request)
            return httpx.Response(200, json={"access_token": "test-access-token"})

        assert str(request.url) == "https://api.giga.chat/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-access-token"
        payload = json.loads(request.content)
        assert payload["model"] == "GigaChat"
        assert payload["messages"][0]["role"] == "system"
        assert "Задач слишком много." in payload["messages"][1]["content"]
        chat_requests.append(request)
        content = json.dumps({
            "sentiment": "negative",
            "topics": ["workload"],
            "problem_topics": ["workload"],
            "summary": "Стажёр сообщает о перегрузке.",
            "evidence": ["Задач слишком много.", "Выдуманная цитата"],
            "confidence": 0.9,
        })
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client_cls = httpx.Client
    transport = httpx.MockTransport(handle)

    def mock_client(**kwargs):
        return client_cls(transport=transport, **kwargs)

    monkeypatch.setattr(llm.httpx, "Client", mock_client)
    provider = llm.GigaChatProvider()
    for _ in range(2):
        result = provider.analyze("Задач слишком много.", "intern")
        assert result.result.problem_topics == ["workload"]
        assert result.result.evidence == ["Задач слишком много."]
    assert len(oauth_requests) == 1
    assert len(chat_requests) == 2


@pytest.mark.parametrize(
    "update",
    (
        {"topics": ["other-invalid"], "problem_topics": ["other-invalid"]},
        {"problem_topics": ["team"]},
        {"evidence": ["Этой цитаты нет в тексте."]},
        {"confidence": "0.9"},
    ),
)
def test_remote_result_rejects_invalid_fields_and_ungrounded_complaints(update):
    payload = {
        "sentiment": "negative",
        "topics": ["workload"],
        "problem_topics": ["workload"],
        "summary": "Стажёр сообщает о перегрузке.",
        "evidence": ["Задач слишком много."],
        "confidence": 0.9,
        **update,
    }
    with pytest.raises(llm.LLMError):
        llm._parse_remote_result(json.dumps(payload), "Задач слишком много.")


def test_empty_remote_result_is_a_failed_analysis():
    with pytest.raises(llm.LLMError):
        llm._parse_remote_result("{}", "Всё получилось.")
