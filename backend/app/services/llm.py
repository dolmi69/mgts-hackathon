from __future__ import annotations

import json
import logging
import re
import ssl
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any
from uuid import uuid4

import certifi
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import BASE_DIR, settings
from ..prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from ..schemas import AIResult, Sentiment

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class UnquotedComplaint(LLMError):
    """The labels are valid, but no quote confirms the complaint."""

    def __init__(self, result: AIResult) -> None:
        super().__init__("LLM complaint has no matching quote", retryable=True)
        self.result = result


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
    # Intent to leave. Matched by _has_leave_intent() with a negation check,
    # never by plain substring search in _mock_topics().
    "leave": (
        "о выходе",
        "об уходе",
        "об увольнении",
        "уйти",
        "уходить",
        "ухожу",
        "уйду",
        "уволиться",
        "увольняться",
        "уволюсь",
        "увольняюсь",
        "другую работу",
        "новую работу",
        "другое место",
        "получил оффер",
        "получила оффер",
        "получил предложение",
        "получила предложение",
        "оффер в друг",
        "есть оффер",
    ),
}

TOPIC_LABELS = {
    "workload": "нагрузка",
    "tasks": "задачи",
    "mentorship": "наставничество",
    "team": "команда",
    "process": "процессы",
    "growth": "развитие",
    "leave": "намерение уйти",
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


NEGATION_WORDS = frozenset({"не", "ни", "нет", "никуда", "незачем", "никогда"})
CLAUSE_RE = re.compile(r"[.,;:!?()]|\s(?:но|а|однако|хотя)\s|\s[-—–]\s")
WORD_RE = re.compile(r"[а-яёa-z]+")
LEAVE_NOT_QUITTING = ("отпуск", "больничн", "обед", "домой", "пораньше", "раньше")


def _leave_negated(clause: str, start: int, end: int) -> bool:
    """«Не думаю уходить», «уходить не собираюсь», «никуда не уйду»."""
    before = WORD_RE.findall(clause[:start])[-2:]
    after = WORD_RE.findall(clause[end:])[:2]
    if any(word.startswith(LEAVE_NOT_QUITTING) for word in after):
        return True  # «ухожу в отпуск» is not quitting
    return any(word in NEGATION_WORDS for word in before + after)


def _has_leave_intent(text: str) -> bool:
    for clause in CLAUSE_RE.split(text.lower()):
        for keyword in TOPIC_KEYWORDS["leave"]:
            start = clause.find(keyword)
            while start != -1:
                if not _leave_negated(clause, start, start + len(keyword)):
                    return True
                start = clause.find(keyword, start + 1)
    return False


def _mock_topics(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if topic == "leave":
            continue
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
        leave = _has_leave_intent(text)
        if leave:
            # An intent to leave is a problem even without complaint words.
            problem_topics = [topic for topic in problem_topics if topic != "other"]
            problem_topics.append("leave")
            topics.append("leave")
            if sentiment == "positive":
                sentiment = "mixed"
            elif sentiment == "neutral":
                sentiment = "negative"
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
        if leave:
            leave_evidence = [s for s in evidence_source if _has_leave_intent(s)]
            evidence = leave_evidence + [s for s in evidence if s not in leave_evidence]
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
            confidence=0.75 if (negative or positive or leave) else 0.45,
        )
        return ProviderResult(result=result, raw=result.model_dump(), model="mock")


# --- remote providers -------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise LLMError("LLM response has no text content")
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
            raise LLMError("LLM did not return JSON") from exc
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc2:
            raise LLMError("LLM returned invalid JSON") from exc2
    if not isinstance(parsed, dict):
        raise LLMError("LLM returned JSON that is not an object")
    return parsed


MIN_QUOTE_CHARS = 8


def is_valid_quote(quote: str, comment: str) -> bool:
    """A quote must be a real, whole-word fragment of the comment.

    «я» or «нравится» cut out of «не нравится» are not evidence.
    """
    quote = (quote or "").strip()
    if not quote or len(quote) < min(MIN_QUOTE_CHARS, len(comment.strip())):
        return False
    for match in re.finditer(re.escape(quote), comment):
        start, end = match.span()
        if (start and comment[start - 1].isalnum()) or (end < len(comment) and comment[end].isalnum()):
            continue
        before = comment[:start].rstrip().lower()
        if before.endswith((" не", " ни")) or before in {"не", "ни"}:
            continue  # the quote drops a negation
        return True
    return False


def _validate_result(result: AIResult, comment: str) -> AIResult:
    # Evidence must be an exact substring; never show invented quotes.
    result.evidence = [item for item in result.evidence if is_valid_quote(item, comment)][:3]
    result.topics = [item for item in result.topics if item][:8]
    result.problem_topics = [item for item in result.problem_topics if item in result.topics][:8]
    if result.sentiment in {"negative", "mixed"} and not result.evidence:
        result.confidence = min(result.confidence, 0.35)
    return result


class _RemoteResult(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    sentiment: Sentiment
    topics: list[str]
    problem_topics: list[str]
    summary: str = Field(min_length=1, max_length=600)
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)


def _parse_remote_result(text: str, comment: str, *, strict: bool = True) -> AIResult:
    try:
        validated = _RemoteResult.model_validate(_extract_json(text))
    except ValidationError as exc:
        raise LLMError("LLM response does not match the required schema") from exc
    if not strict:
        # The repair round did not help. An invented code must not lose the
        # whole answer, nor reach the UI: a complaint becomes "other".
        known = lambda codes: list(dict.fromkeys(code if code in TOPIC_LABELS else "other" for code in codes))  # noqa: E731
        validated.problem_topics = known(validated.problem_topics)
        validated.topics = list(dict.fromkeys(
            [code for code in validated.topics if code in TOPIC_LABELS] + validated.problem_topics
        ))
    if set(validated.topics) - TOPIC_LABELS.keys():
        raise LLMError("LLM response contains unknown topics")
    if set(validated.problem_topics) - set(validated.topics):
        raise LLMError("LLM response lists a problem absent from its topics")
    if validated.problem_topics and validated.sentiment in {"positive", "neutral"}:
        # The model found a complaint, so the tone cannot be complaint-free.
        validated.sentiment = "mixed"
    result = _validate_result(AIResult.model_validate(validated.model_dump()), comment)
    if (result.problem_topics or result.sentiment in {"mixed", "negative"}) and not result.evidence:
        # Sampling differs between calls: a new attempt usually quotes correctly.
        raise UnquotedComplaint(result)
    return result


def _temporary_error(exc: Exception) -> bool:
    if isinstance(exc, LLMError):
        return exc.retryable
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
    return isinstance(exc, httpx.TransportError)


def _unquoted_result(exc: UnquotedComplaint, model: str) -> ProviderResult:
    # Losing a complaint is worse than showing it without a quote: HR always
    # sees the original comment. Low confidence marks the missing proof.
    exc.result.evidence = []
    exc.result.confidence = min(exc.result.confidence, 0.35)
    return ProviderResult(result=exc.result, raw={"guard": "no matching quote"}, model=model)


def _failure_reason(exc: Exception) -> str:
    """A diagnosis without response bodies: they may quote the comment."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc) if isinstance(exc, LLMError) else type(exc).__name__


def _repair_prompt() -> str:
    return (
        "Предыдущий ответ не прошёл проверку. Верни только валидный JSON "
        "со всеми полями схемы, без markdown. evidence — точные подстроки комментария. "
        "Для жалобы нужна хотя бы одна подтверждающая цитата. Проверь оба списка тем: "
        "workload, tasks, mentorship, team, process, growth, leave, other — исчерпывающий список кодов. "
        "В частности, доступы — process, поведение коллег — team, условия офиса — other, "
        "явное намерение уйти, поиск другой работы или оффер в другом месте — leave "
        "(обычная жалоба без намерения уйти — не leave)."
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
                # Some compatible providers reject response_format. Remember it,
                # so retries and repair rounds do not pay for the failing call again.
                self._json_mode = False
                return self._request(messages, json_mode=False)
            response.raise_for_status()
            return response.json()

    def _parse(self, body: dict[str, Any], comment: str, *, strict: bool = True) -> tuple[AIResult, str]:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected LLM response") from exc
        return _parse_remote_result(content, comment, strict=strict), content

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
        for attempt in range(self.max_retries + 1):
            try:
                body = self._request(messages, self._json_mode)
                try:
                    result, content = self._parse(body, comment)
                except LLMError:
                    repair = {"role": "user", "content": messages[-1]["content"] + "\n" + _repair_prompt()}
                    body = self._request([messages[0], repair], self._json_mode)
                    result, content = self._parse(body, comment, strict=False)
                return ProviderResult(
                    result=result,
                    raw={"content": content, "body": body},
                    model=str(body.get("model") or self.model),
                )
            except (httpx.HTTPError, LLMError, ValueError) as exc:
                retryable = _temporary_error(exc)
                if retryable and attempt < self.max_retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if isinstance(exc, UnquotedComplaint):
                    return _unquoted_result(exc, self.model)
                raise LLMError(f"LLM request or validation failed: {_failure_reason(exc)}", retryable=retryable) from exc
        raise AssertionError("The initial LLM attempt was skipped")


class GigaChatProvider(OpenAICompatibleProvider):
    """Use the shared chat parser with GigaChat's OAuth and HTTP endpoints."""

    name = "gigachat"
    oauth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    def __init__(self) -> None:
        super().__init__()
        self.base_url = settings.gigachat_base_url.rstrip("/")
        self.model = settings.gigachat_model
        self._json_mode = False
        self._tls = ssl.create_default_context(cafile=certifi.where())
        if settings.gigachat_ca_bundle:
            self._tls.load_verify_locations(cafile=BASE_DIR / settings.gigachat_ca_bundle)
        self._token_lock = Lock()
        self._access_token = ""
        self._token_deadline = 0.0

    def _token(self, client: httpx.Client) -> str:
        with self._token_lock:
            if time.monotonic() >= self._token_deadline:
                response = client.post(
                    self.oauth_url,
                    headers={
                        "Authorization": f"Basic {settings.llm_api_key}",
                        "RqUID": str(uuid4()),
                        "Accept": "application/json",
                    },
                    data={"scope": settings.gigachat_scope},
                )
                response.raise_for_status()
                body = response.json()
                token = body.get("access_token") if isinstance(body, dict) else None
                if not isinstance(token, str) or not token:
                    raise LLMError("GigaChat OAuth response has no access token")
                self._access_token = token
                # Refresh before GigaChat's 30-minute lifetime expires.
                self._token_deadline = time.monotonic() + 25 * 60
            return self._access_token

    def _request(self, messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, verify=self._tls) as client:
            token = self._token(client)
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": max(0.01, settings.llm_temperature),
                    "max_tokens": 700,
                },
            )
            if response.status_code == 401:
                with self._token_lock:
                    if self._access_token == token:
                        self._token_deadline = 0.0
                raise LLMError("GigaChat rejected the access token", retryable=True)
            response.raise_for_status()
            return response.json()


@lru_cache(maxsize=1)
def _gigachat_provider() -> GigaChatProvider:
    return GigaChatProvider()


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
            # Ask Yandex Cloud not to keep request texts for model training.
            "x-data-logging-enabled": "false",
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
        for attempt in range(self.max_retries + 1):
            try:
                body = self._request(messages, settings.llm_temperature)
                try:
                    text = body["result"]["alternatives"][0]["message"]["text"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise LLMError("Unexpected YandexGPT response") from exc
                result = _parse_remote_result(text, comment, strict=False)
                return ProviderResult(
                    result=result,
                    raw={"content": text, "body": body},
                    model=self.model,
                )
            except (httpx.HTTPError, LLMError, ValueError) as exc:
                retryable = _temporary_error(exc)
                if retryable and attempt < self.max_retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if isinstance(exc, UnquotedComplaint):
                    return _unquoted_result(exc, self.model)
                raise LLMError(f"YandexGPT request or validation failed: {_failure_reason(exc)}", retryable=retryable) from exc
        raise AssertionError("The initial YandexGPT attempt was skipped")


def get_provider() -> BaseProvider:
    provider = (settings.llm_provider or "mock").lower()
    if provider == "mock":
        return MockProvider()
    if provider in {"openai", "openai-compatible", "deepseek"}:
        return OpenAICompatibleProvider()
    if provider in {"yandex", "yandexgpt"}:
        return YandexGPTProvider()
    if provider == "gigachat":
        return _gigachat_provider()
    logger.warning("Unknown LLM_PROVIDER=%s, falling back to mock", provider)
    return MockProvider()


def provider_model_name(provider: BaseProvider) -> str:
    if isinstance(provider, MockProvider):
        return "mock"
    return getattr(provider, "model", provider.name)
