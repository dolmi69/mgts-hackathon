from __future__ import annotations

import re
from collections.abc import Iterable

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)?[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)"
)
TELEGRAM_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{4,}")


def redact_pii(text: str, names: Iterable[str] = ()) -> str:
    """Remove obvious PII before sending a comment to an LLM.

    Free text is hard: names can appear inside a sentence. We redact full
    names from the roster and individual name tokens of 3+ characters.
    """
    if not text:
        return ""
    result = EMAIL_RE.sub("[EMAIL]", text)
    result = PHONE_RE.sub("[ТЕЛЕФОН]", result)
    result = TELEGRAM_RE.sub("[КОНТАКТ]", result)

    tokens: set[str] = set()
    for name in names:
        clean = " ".join(str(name or "").split())
        if not clean:
            continue
        tokens.add(clean)
        parts = [part for part in clean.split() if len(part) >= 3]
        tokens.update(parts)

    for token in sorted(tokens, key=len, reverse=True):
        result = re.sub(re.escape(token), "[ИМЯ]", result, flags=re.IGNORECASE)
    return " ".join(result.split())


def has_pii(text: str) -> bool:
    return bool(EMAIL_RE.search(text) or PHONE_RE.search(text) or TELEGRAM_RE.search(text))
