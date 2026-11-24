import pytest

from app.services.anonymize import has_pii, redact_pii


def test_redacts_email_phone_telegram_and_names():
    text = "Анна Смирнова, anna@example.com, +7 (999) 123-45-67, @anna_dev"
    result = redact_pii(text, ["Анна Смирнова", "Олег Кузнецов"])
    assert "[EMAIL]" in result
    assert "[ТЕЛЕФОН]" in result
    assert "[КОНТАКТ]" in result
    assert "[ИМЯ]" in result
    assert "Анна" not in result
    assert not has_pii(result)


def test_keeps_plain_text_without_pii():
    text = "Интересные задачи, наставник помогает."
    assert redact_pii(text, ["Анна Смирнова"]) == text
    assert not has_pii(text)


ROSTER = ["Мария Козлова", "Максим Фёдоров", "Дина Ли", "Марк Орлов"]


def test_redact_names_in_any_case_and_with_yo():
    text = "Спасибо Марии и Козловой, с Марией легко. Федоров и Фёдорову тоже."
    redacted = redact_pii(text, ROSTER)
    for leaked in ("Мари", "Козлов", "Федоров", "Фёдоров"):
        assert leaked not in redacted
    assert redacted.count("[ИМЯ]") == 5


def test_redact_keeps_ordinary_words_that_contain_names():
    text = "Работаю максимально, динамика по маркетингу хорошая. Мне не нравится."
    assert redact_pii(text, ROSTER + ["не", "а", "int-003", "x@y.ru"]) == text


def test_redact_contacts_and_documents():
    text = (
        "t.me/ivan_p, vk.com/id123, иван@почта.рф, +375 29 123-45-67, "
        "СНИЛС 123-456-789 01, паспорт 4510 123456, карта 4276 1234 5678 9012, ИНН 771234567890"
    )
    redacted = redact_pii(text)
    for leaked in ("ivan_p", "id123", "почта", "123-45-67", "789", "123456", "9012", "771234567890"):
        assert leaked not in redacted


def test_redact_keeps_scores_and_dates():
    text = "Работаю 12 часов, оценка 4 из 5, с 2026-03-01."
    assert redact_pii(text) == text


def test_webhook_placeholder_names_are_not_used(db):
    from app.models import Intern
    from app.services.analysis import all_people_names

    db.add_all([
        Intern(external_id="не", name="не"),
        Intern(external_id="int-real", name="Мария Козлова"),
    ])
    db.commit()
    assert all_people_names(db) == ["Мария Козлова"]


# --- Colloquial forms of roster names -----------------------------------------

VARIANT_ROSTER = ["Мария Козлова", "Александр Петров", "Пётр Сидоров", "Дмитрий Орлов", "Олег Ким"]


def test_redacts_diminutives_of_roster_names_in_any_case():
    text = "Маша и Машенька помогли, Машу хвалили. Саша с Шурой, Петя и Пете, Дима, Олежка."
    redacted = redact_pii(text, VARIANT_ROSTER, ner=False)
    for leaked in ("Маш", "Саш", "Шур", "Пет", "Дим", "Олеж"):
        assert leaked not in redacted
    assert redacted.count("[ИМЯ]") == 9


def test_diminutives_only_for_names_in_roster():
    text = "Катя и Серёжа помогли."
    assert redact_pii(text, VARIANT_ROSTER, ner=False) == text
    assert redact_pii(text, ["Екатерина Ли", "Сергей Ким"], ner=False) == "[ИМЯ] и [ИМЯ] помогли."


def test_diminutives_with_yo_folding():
    assert redact_pii("Лёша и Леше спасибо", ["Алексей Ким"], ner=False) == "[ИМЯ] и [ИМЯ] спасибо"


def test_diminutives_do_not_eat_common_words():
    roster = ["Артём Белов", "Людмила Ким", "Лев Ли", "Любовь Орлова", "Леонид Ким"]
    text = "Тема интересная, задача понятная, тему сменили. Люди хорошие, левой кнопкой, любой ценой, от лени."
    assert redact_pii(text, roster, ner=False) == text


def test_variant_dictionary_excludes_colliding_forms():
    from app.services.name_variants import NAME_VARIANTS

    variants = {v.lower().replace("ё", "е") for forms in NAME_VARIANTS.values() for v in forms}
    for word in ("тема", "поля", "тоня", "ник", "мила"):
        assert word not in variants


# --- Optional NER (Natasha) ---------------------------------------------------


def test_ner_off_keeps_names_outside_roster():
    text = "Коллега Андрей помог"
    assert redact_pii(text, VARIANT_ROSTER, ner=False) == text


def test_ner_redacts_people_outside_roster():
    pytest.importorskip("natasha")
    cases = {
        "Руководитель Сергей Иванович опять сорвал сроки": "Руководитель [ИМЯ] опять сорвал сроки",
        "Спасибо Ольге Петровне из HR": "Спасибо [ИМЯ] из HR",
        "Коллега Андрей помог": "Коллега [ИМЯ] помог",
    }
    for text, expected in cases.items():
        assert redact_pii(text, ner=True) == expected


def test_ner_keeps_work_words():
    pytest.importorskip("natasha")
    for text in ("Работаю в МГТС", "Задачи по Python", "Дедлайн в понедельник", "Тема интересная, задача понятная"):
        assert redact_pii(text, ner=True) == text


def test_ner_combines_with_roster_and_contacts():
    pytest.importorskip("natasha")
    text = "Маша и наставник Сергей Иванович, пишите anna@example.com"
    redacted = redact_pii(text, ["Мария Козлова"], ner=True)
    assert redacted == "[ИМЯ] и наставник [ИМЯ], пишите [EMAIL]"


def test_ner_missing_falls_back_to_regex(monkeypatch):
    from app.services import anonymize

    monkeypatch.setattr(anonymize, "_NER", None)
    monkeypatch.setattr(anonymize, "_NER_LOADED", True)
    assert redact_pii("Коллега Андрей, Маша", ["Мария Ли"], ner=True) == "Коллега Андрей, [ИМЯ]"
