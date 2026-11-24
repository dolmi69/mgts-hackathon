from app.schemas import AIResult
from app.services.llm import MockProvider, _validate_result


def test_mock_detects_polite_complaint():
    comment = "Всё нравится, но задач стало слишком много."
    result = MockProvider().analyze(comment, "intern")
    assert result.result.sentiment == "mixed"
    assert "workload" in result.result.problem_topics or "tasks" in result.result.problem_topics
    assert result.result.evidence
    assert all(quote in comment for quote in result.result.evidence)


def test_mock_keeps_positive_comment_positive():
    comment = "Интересные задачи, наставник помогает, всё нравится."
    result = MockProvider().analyze(comment, "intern")
    assert result.result.sentiment == "positive"
    assert result.result.problem_topics == []
    assert all(quote in comment for quote in result.result.evidence)


def test_validate_result_drops_invented_evidence():
    comment = "Задачи интересные, но не хватает обратной связи."
    result = AIResult(
        sentiment="negative",
        topics=["mentorship"],
        problem_topics=["mentorship"],
        summary="Есть жалоба.",
        evidence=["этой цитаты нет в тексте"],
        confidence=0.9,
    )
    validated = _validate_result(result, comment)
    assert validated.evidence == []
    assert validated.confidence <= 0.35


def test_mock_detects_intent_to_leave():
    comment = "Думаю о выходе, ищу другую работу."
    result = MockProvider().analyze(comment, "intern").result
    assert "leave" in result.topics
    assert "leave" in result.problem_topics
    assert result.sentiment == "negative"
    assert result.evidence and all(quote in comment for quote in result.evidence)


def test_mock_ignores_negated_intent_to_leave():
    for comment in (
        "Уходить не собираюсь, всё нравится.",
        "Не думаю уходить, команда отличная.",
        "В пятницу ухожу в отпуск.",
    ):
        result = MockProvider().analyze(comment, "intern").result
        assert "leave" not in result.topics, comment
        assert "leave" not in result.problem_topics, comment
