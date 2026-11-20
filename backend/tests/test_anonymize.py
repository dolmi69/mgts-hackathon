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
