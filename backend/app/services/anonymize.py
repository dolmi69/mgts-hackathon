"""Strip personal data from a comment before it leaves for an LLM.

Three layers, applied in order:

1. regexes for contacts and document numbers;
2. optional NER (Natasha, if installed and ``anonymizer_ner`` is on) for
   person names that are not in the roster: managers, colleagues;
3. roster names in any grammatical case, plus colloquial forms of roster
   first names (Мария → Маша, Машу, Машей) from ``name_variants``.

It is a safety net, not a guarantee: a surname written in lower case, a rare
nickname or a nickname of someone outside the roster may still pass.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

from .name_variants import COMMON_WORD_FORMS, NAME_VARIANTS

logger = logging.getLogger(__name__)

FLAGS = re.IGNORECASE | re.UNICODE

# Order matters: long number formats first, so a card is not taken for a phone.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|vk\.com|vk\.ru|ok\.ru|instagram\.com|facebook\.com)/\S+", FLAGS), "[КОНТАКТ]"),
    (re.compile(r"[\w.%+\-]+(?:@|\(at\)|\[at\])[\w\-]+(?:\.[\w\-]+)*\.\w{2,}", FLAGS), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\d{4}[ \-]?){3}\d{4}(?:[ \-]?\d{1,3})?(?!\d)"), "[НОМЕР]"),  # bank card
    (re.compile(r"(?<!\d)\d{3}[ \-]?\d{3}[ \-]?\d{3}[ \-]\d{2}(?!\d)"), "[НОМЕР]"),  # SNILS
    (re.compile(r"(?<!\d)\d{2}[ ]?\d{2}[ ]?№?[ ]?\d{6}(?!\d)"), "[НОМЕР]"),  # passport
    (re.compile(r"(?<![\w\d])(?:\+\d{1,3}|8)?[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
    (re.compile(r"(?<![\w\d])\+\d{1,3}[\s\-()]*\d{2,3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)"), "[ТЕЛЕФОН]"),
    (re.compile(r"(?<!\d)\d{9,}(?!\d)"), "[НОМЕР]"),  # INN, account and other long IDs
    (re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}"), "[КОНТАКТ]"),
]

# Russian case endings for given names and surnames (Мария → Марии, Марией;
# Козлова → Козловой; Фёдоров → Фёдорову). Matched only up to a word boundary,
# so «Максим» does not hit «максимально».
CASE_ENDINGS = (
    "ами", "ями", "ого", "его", "ому", "ему", "ой", "ей", "ом", "ем", "ою", "ею",
    "ым", "им", "ых", "их", "ую", "юю", "ая", "яя", "ов", "ев", "ам", "ям", "ах", "ях",
    "а", "я", "у", "ю", "е", "ы", "и", "ь", "й", "о",
)
STEM_VOWELS = "аяйьоеыиую"
NAME_TOKEN_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}$")
MIN_NAME_TOKEN = 3


def _fold(text: str) -> str:
    """Case- and ё-insensitive form with the same length as the input."""
    return text.lower().replace("ё", "е")


def _name_stems(names: Iterable[str]) -> set[str]:
    stems: set[str] = set()
    for name in names:
        for part in str(name or "").split():
            part = part.strip(".,;:()\"'«»")
            # Skip IDs, emails and initials: «int-003», «a@b», «И.».
            if len(part) < MIN_NAME_TOKEN or not NAME_TOKEN_RE.match(part):
                continue
            token = _fold(part)
            stem = token[:-1] if token[-1] in STEM_VOWELS and len(token) > MIN_NAME_TOKEN else token
            stems.add(stem)
    return stems


# Endings of colloquial forms, by declension: «Маша/Маши/Машей»,
# «Лена/Лены/Леной», «Оля/Оли/Олей», «Макс/Макса/Максом». Kept per type so a
# soft name does not pick up hard endings: «Люда» must not hit «люди».
VARIANT_HARD_ENDINGS = ("а", "ы", "е", "у", "ой", "ою")
VARIANT_HUSHING_ENDINGS = ("а", "и", "е", "у", "ой", "ей", "ою", "ею")  # after ж ш ч щ к г х
VARIANT_SOFT_ENDINGS = ("я", "и", "е", "ю", "ей", "ею")
VARIANT_CONSONANT_ENDINGS = ("а", "у", "ом", "ем", "е", "ы", "и")
_VARIANTS_BY_NAME: dict[str, tuple[str, ...]] = {}
for _full, _forms in NAME_VARIANTS.items():
    _key = _full.lower().replace("ё", "е")
    _VARIANTS_BY_NAME[_key] = _VARIANTS_BY_NAME.get(_key, ()) + tuple(_forms)


def variant_forms(variant: str) -> tuple[str, tuple[str, ...], bool]:
    """(folded stem, endings, ending required) for a colloquial name form."""
    token = _fold(variant)
    if token.endswith("я"):
        return token[:-1], VARIANT_SOFT_ENDINGS, True
    if token.endswith("а"):
        hushing = token[-2] in "жшчщкгх"
        return token[:-1], VARIANT_HUSHING_ENDINGS if hushing else VARIANT_HARD_ENDINGS, True
    return token, VARIANT_CONSONANT_ENDINGS, False


def _variant_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    """Colloquial forms of roster first names, in any case."""
    groups: dict[tuple[tuple[str, ...], bool], set[str]] = {}
    for name in names:
        for part in str(name or "").split():
            for variant in _VARIANTS_BY_NAME.get(_fold(part.strip(".,;:()\"'«»")), ()):
                stem, endings, required = variant_forms(variant)
                groups.setdefault((endings, required), set()).add(stem)
    if not groups:
        return None
    alternatives = []
    for (endings, required), stems in groups.items():
        stem_re = "|".join(re.escape(s) for s in sorted(stems, key=len, reverse=True))
        alternatives.append(rf"(?:{stem_re})(?:{'|'.join(endings)}){'' if required else '?'}")
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)")


def _name_pattern(stems: set[str]) -> re.Pattern[str] | None:
    if not stems:
        return None
    alternatives = "|".join(re.escape(stem) for stem in sorted(stems, key=len, reverse=True))
    endings = "|".join(CASE_ENDINGS)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?:{endings})?(?!\w)")


# --- Optional NER -------------------------------------------------------------

_NER_LOCK = threading.Lock()
_NER: Callable[[str], list[tuple[int, int]]] | None = None
_NER_LOADED = False


def _load_ner() -> Callable[[str], list[tuple[int, int]]] | None:
    """Build the Natasha pipeline once per process; None if it is unavailable."""
    global _NER, _NER_LOADED
    if _NER_LOADED:
        return _NER
    with _NER_LOCK:
        if _NER_LOADED:
            return _NER
        try:
            from natasha import PER, Doc, NewsEmbedding, NewsNERTagger, Segmenter
        except ImportError:
            logger.info("natasha is not installed: names outside the roster are not redacted")
        else:
            try:
                segmenter = Segmenter()
                tagger = NewsNERTagger(NewsEmbedding())
                # Slovnet models are plain numpy, but keep one call at a time.
                call_lock = threading.Lock()

                def find_people(text: str) -> list[tuple[int, int]]:
                    doc: Any = Doc(text)
                    with call_lock:
                        doc.segment(segmenter)
                        doc.tag_ner(tagger)
                    return [(span.start, span.stop) for span in doc.spans if span.type == PER]

                _NER = find_people
            except Exception:  # noqa: BLE001 - a broken model must not stop analysis
                logger.exception("natasha NER failed to initialise; using regexes only")
        _NER_LOADED = True
        return _NER


def ner_available() -> bool:
    return _load_ner() is not None


def _ner_enabled() -> bool:
    try:
        from ..config import settings
    except Exception:  # noqa: BLE001 - used standalone, without app settings
        return True
    return bool(getattr(settings, "anonymizer_ner", True))


def _redact_ner(text: str) -> str:
    find_people = _load_ner()
    if find_people is None:
        return text
    try:
        spans = find_people(text)
    except Exception:  # noqa: BLE001 - fall back to regexes on a model error
        logger.exception("natasha NER failed on a comment; using regexes only")
        return text
    for start, stop in sorted(spans, reverse=True):
        text = text[:start] + "[ИМЯ]" + text[stop:]
    return text


def _replace(text: str, pattern: re.Pattern[str], skip: frozenset[str] = frozenset()) -> str:
    folded = _fold(text)
    pieces: list[str] = []
    last = 0
    # Match on the folded copy; it has the same offsets as the original.
    for match in pattern.finditer(folded):
        if match.group(0) in skip:
            continue
        pieces.append(text[last : match.start()])
        pieces.append("[ИМЯ]")
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def redact_pii(text: str, names: Iterable[str] = (), *, ner: bool | None = None) -> str:
    """Remove contacts, document numbers and person names, keeping other words intact.

    ``ner`` forces NER on or off; by default it follows ``settings.anonymizer_ner``.
    """
    if not text:
        return ""
    names = list(names)
    result = text
    for pattern, label in PATTERNS:
        result = pattern.sub(label, result)

    if ner if ner is not None else _ner_enabled():
        result = _redact_ner(result)

    pattern = _name_pattern(_name_stems(names))
    if pattern is not None:
        result = _replace(result, pattern, COMMON_WORD_FORMS)
    variants = _variant_pattern(names)
    if variants is not None:
        result = _replace(result, variants, COMMON_WORD_FORMS)
    return " ".join(result.split())


def has_pii(text: str) -> bool:
    return any(pattern.search(text) for pattern, _ in PATTERNS)
