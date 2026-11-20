from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings
from ..prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from ..schemas import AIResult

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    result: AIResult
    raw: dict[str, Any]
    model: str
    prompt_version: str = PROMPT_VERSION


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    def analyze(
        self,
        comment: str,
        respondent_type: str,
        month_index: int | None = None,
    ) -> ProviderResult:
        raise NotImplementedError


# --- deterministic local provider -------------------------------------------------

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "workload": (
        "нагруз",
        "переработ",
        "много задач",
        "объём",
        "объем",
        "устаю",
        "устал",
        "завал",
        "перегруз",
        "бесконечные правки",
        "не хватает времени",
        "много работы",
    ),
    "tasks": (
        "задач",
        "проект",
        "правк",
        "интересн",
        "скучн",
        "рутин",
        "код",
        "аналитик",
        "исследован",
    ),
    "mentorship": (
        "наставник",
        "ментор",
        "обратной связи",
        "обратная связь",
        "помощ",
        "поддержк",
        "объясня",
        "развива",
        "подсказ",
    ),
    "team": ("команд", "коллег", "атмосфер", "общени", "люди"),
    "process": (
        "процесс",
        "организац",
        "бюрократ",
        "согласован",
        "регламент",
        "доступ",
        "инструмент",
        "онбординг",
    ),
    "growth": ("рост", "развит", "обучен", "карьер", "навык", "опыт"),
}

TOPIC_LABELS = {
    "workload": "нагрузка",
    "tasks": "задачи",
    "mentorship": "наставничество",
    "team": "команда",
    "process": "процессы",
    "growth": "развитие",
    "other": "другое",
}

NEGATIVE_MARKERS = (
    "слишком",
    "не хватает",
    "нехват",
    "устаю",
    "устал",
    "тяжело",
    "проблем",
    "сложно",
    "не устраивает",
    "бесконечн",
    "переработ",
    "стресс",
    "выгор",
    "разочаров",
    "непонятн",
    "хаос",
    "завал",
    "давлен",
    "мало",
    "плохо",
    "жалоб",
    "мешает",
    "неудобн",
    "скучн",
    "бросил",
    "нет обратной связи",
    "нет поддержки",
    "не дают",
)

POSITIVE_MARKERS = (
    "нравится",
    "отличн",
    "интересн",
    "доволен",
    "довольна",
    "супер",
    "класс",
    "помогает",
    "удобно",
    "хорошо",
    "спасибо",
    "рад",
    "прогресс",
    "полезно",
    "понятно",
    "круто",
)

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(marker in low for marker in markers)


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _mock_topics(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in low for keyword in keywords):
            found.append(topic)
    return found


def _mock_problem_topics(text: str, topics: list[str]) -> list[str]:
    low = text.lower()
    if not _contains_any(low, NEGATIVE_MARKERS):
        return []
    if not topics:
        return ["other"]
    result: list[str] = []
    for topic in topics:
        keywords = TOPIC_KEYWORDS.get(topic, ())
        if any(keyword in low for keyword in keywords):
            result.append(topic)
    return result or ["other"]


class MockProvider(BaseProvider):
    """Fast deterministic provider for local demos and tests.

    It is intentionally simple: keyword sentiment and topic extraction.
    Real LLM providers are used when configured.
    """

    name = "mock"

    def analyze(
        self,
        comment: str,
        respondent_type: str,
        month_index: int | None = None,
    ) -> ProviderResult:
        text = comment.strip()
        if not text:
            result = AIResult(
                sentiment="neutral",
                summary="Комментарий отсутствует.",
                confidence=0.3,
            )
            return ProviderResult(result=result, raw=result.model_dump(), model="mock")

        negative = _contains_any(text, NEGATIVE_MARKERS)
        positive = _contains_any(text, POSITIVE_MARKERS)
        if negative and positive:
            sentiment = "mixed"
        elif negative:
            sentiment = "negative"
        elif positive:
            sentiment = "positive"
        else:
            sentiment = "neutral"

        topics = _mock_topics(text)
        problem_topics = _mock_problem_topics(text, topics)
        if problem_topics and "other" in problem_topics and "other" not in topics:
            topics.append("other")

        evidence_source = _sentences(text)
        if sentiment in {"negative", "mixed"}:
            evidence = [
                sentence
                for sentence in evidence_source
                if _contains_any(sentence, NEGATIVE_MARKERS)
            ]
        else:
            evidence = [
                sentence
                for sentence in evidence_source
                if _contains_any(sentence, POSITIVE_MARKERS)
            ]
        if not evidence and evidence_source:
            evidence = [evidence_source[0]]
        evidence = evidence[:3]

        problem_labels = ", ".join(
            TOPIC_LABELS.get(topic, topic) for topic in problem_topics
        )
        if sentiment == "negative":
            summary = (
                "В комментарии есть жалобы"
                + (f": {problem_labels}" if problem_labels else "")
                + "."
            )
        elif sentiment == "mixed":
            summary = (
                "Есть и позитив, и проблемы"
                + (f": {problem_labels}" if problem_labels else "")
                + "."
            )
        elif sentiment == "positive":
            summary = "Комментарий в целом позитивный."
        else:
            summary = "Нейтральный комментарий без явных сигналов."

        result = AIResult(
            sentiment=sentiment,
            topics=topics,
            problem_topics=problem_topics,
            summary=summary,
            evidence=evidence,
            confidence=0.75 if (negative or positive) else 0.45,
        )
        return ProviderResult(result=result, raw=result.model_dump(), model="mock")


# --- remote providers -------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"LLM did not return JSON: {raw[:300]}") from exc
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc2:
            raise LLMError(f"LLM returned invalid JSON: {raw[:300]}") from exc2
    if not isinstance(parsed, dict):
        raise LLMError("LLM returned JSON that is not an object")
    return parsed


def _validate_result(result: AIResult, comment: str) -> AIResult:
    # Evidence must be an exact substring; never show invented quotes.
    result.evidence = [item for item in result.evidence if item and item in comment][:3]
    result.topics = [item for item in result.topics if item][:8]
    result.problem_topics = [item for item in result.problem_topics if item in result.topics][:8]
    if result.sentiment in {"negative", "mixed"} and not result.evidence:
        result.confidence = min(result.confidence, 0.35)
    return result


def _repair_prompt() -> str:
    return (
        "Предыдущий ответ не прошёл проверку. Верни только валидный JSON "
        "по заданной схеме, без markdown. evidence — точные подстроки комментария."
    )


class OpenAICompatibleProvider(BaseProvider):
    """Provider for OpenAI-compatible chat completions (DeepSeek etc.)."""

    name = "openai"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not configured")
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout_seconds
        self.max_retries = max(0, settings.llm_max_retries)
        self._json_mode = True

    def _request(self, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            if response.status_code == 400 and json_mode:
                # Some compatible providers reject response_format.
                return self._request(messages, json_mode=False)
            response.raise_for_status()
            return response.json()

    def _parse(self, body: dict[str, Any]) -> tuple[AIResult, str]:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response: {body}") from exc
        data = _extract_json(content)
        result = AIResult.model_validate(data)
        return result, content

    def analyze(
        self,
        comment: str,
        respondent_type: str,
        month_index: int | None = None,
    ) -> ProviderResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(comment, respondent_type, month_index),
            },
        ]
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                body = self._request(messages, self._json_mode)
                try:
                    result, content = self._parse(body)
                except LLMError:
                    # A repair round. Some models wrap JSON in markdown.
                    body = self._request(
                        messages + [{"role": "user", "content": _repair_prompt()}],
                        self._json_mode,
                    )
                    result, content = self._parse(body)
                result = _validate_result(result, comment)
                return ProviderResult(
                    result=result,
                    raw={"content": content, "body": body},
                    model=self.model,
                )
            except (httpx.HTTPError, LLMError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
        raise LLMError(f"LLM request failed: {last_error}")


class YandexGPTProvider(BaseProvider):
    """YandexGPT completion API provider."""

    name = "yandex"
    endpoint = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not configured")
        if not settings.llm_folder_id:
            raise LLMError("LLM_FOLDER_ID is not configured for YandexGPT")
        self.model = settings.llm_model or "yandexgpt-lite"
        self.folder_id = settings.llm_folder_id
        self.max_retries = max(0, settings.llm_max_retries)

    def _request(self, messages: list[dict[str, str]], temperature: float) -> dict[str, Any]:
        payload = {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": "1200",
            },
            "messages": [
                {"role": item["role"], "text": item["content"]} for item in messages
            ],
        }
        headers = {
            "Authorization": f"Api-Key {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    def analyze(
        self,
        comment: str,
        respondent_type: str,
        month_index: int | None = None,
    ) -> ProviderResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(comment, respondent_type, month_index),
            },
        ]
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                body = self._request(messages, settings.llm_temperature)
                try:
                    text = body["result"]["alternatives"][0]["message"]["text"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise LLMError(f"Unexpected YandexGPT response: {body}") from exc
                data = _extract_json(text)
                result = _validate_result(AIResult.model_validate(data), comment)
                return ProviderResult(
                    result=result,
                    raw={"content": text, "body": body},
                    model=self.model,
                )
            except (httpx.HTTPError, LLMError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
        raise LLMError(f"YandexGPT request failed: {last_error}")


def get_provider() -> BaseProvider:
    provider = (settings.llm_provider or "mock").lower()
    if provider == "mock":
        return MockProvider()
    if provider in {"openai", "openai-compatible", "deepseek"}:
        return OpenAICompatibleProvider()
    if provider in {"yandex", "yandexgpt"}:
        return YandexGPTProvider()
    logger.warning("Unknown LLM_PROVIDER=%s, falling back to mock", provider)
    return MockProvider()


def provider_model_name(provider: BaseProvider) -> str:
    if isinstance(provider, MockProvider):
        return "mock"
    return getattr(provider, "model", provider.name)
