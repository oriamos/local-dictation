"""Deterministic text cleanup (fast, never invents content).

Modes:
  verbatim     – dictionary spelling + spacing only
  clean        – + filler removal, accidental repeats, punctuation spacing  (default)
  professional – clean + capitalized start and terminal punctuation
  developer    – clean, but Latin tokens / paths / identifiers are never altered
The optional local-LLM pass (Phase 3) plugs in after this and is held to the same "no new content" rule.
"""
from __future__ import annotations

import re

HEB = "֐-׿"
PREFIX = "ובלמהשכ"
# Valid Hebrew prefix clusters: a single letter, or ו/ש/כש + one more ("וב", "שה", "כשה"…).
PFX = rf"(?:[{PREFIX}]|[וש][בלמהכש]|כש[הבל]?|וכש|שב|ושה)"

FILLERS_HE = ["אהה+", "אה", "אממ+", "אמ", "המממ*", "אהם", "ממ+"]
FILLERS_EN = ["um+", "uh+", "uhm+", "erm", "er", "hmm+"]
_FILLER_RE = re.compile(
    rf"(?<![{HEB}\w])(?:{'|'.join(FILLERS_HE + FILLERS_EN)})(?![{HEB}\w])[,.]?\s*", re.IGNORECASE)

# Words commonly stuttered; immediate duplicates of these are removed ("את את" -> "את").
# Intentional doubles like "מאוד מאוד" / "לאט לאט" are not in the list and are kept.
STUTTER = {"את", "של", "זה", "אני", "אז", "ש", "על", "עם", "הוא", "היא", "אנחנו", "גם", "כי", "לא", "יש", "מה",
           "the", "a", "an", "i", "to", "and", "is", "it", "that", "of", "in"}


class Dictionary:
    def __init__(self, entries: dict[str, list[str]]):
        pats = []
        for term, variants in entries.items():
            for v in sorted(set(variants) | {term}, key=len, reverse=True):
                pats.append((v, term))
        pats.sort(key=lambda p: len(p[0]), reverse=True)  # longest first: "קלוד קוד" before "קלוד"
        alt = "|".join(re.escape(v).replace(r"\ ", r"[\s\-]?") for v, _ in pats)
        self._lookup = {self._key(v): t for v, t in pats}
        self._re = re.compile(rf"(?<![{HEB}A-Za-z0-9])({PFX}?)-?({alt})(?![{HEB}A-Za-z0-9])", re.IGNORECASE) if pats else None

    @staticmethod
    def _key(s: str) -> str:
        return re.sub(r"[\s\-]", "", s.lower())

    def apply(self, text: str) -> str:
        if not self._re:
            return text

        def sub(m):
            pre, word = m.group(1), m.group(2)
            term = self._lookup.get(self._key(word))
            if term is None:  # prefix letters were part of the word itself
                return m.group(0)
            return f"{pre}-{term}" if pre else term

        return self._re.sub(sub, text)

    def prompt(self, limit=40) -> str:
        """Short context for Whisper's initial_prompt: biases spelling of known terms."""
        terms = list(self._lookup.values())
        seen, out = set(), []
        for t in terms:
            if t not in seen:
                seen.add(t); out.append(t)
        return ", ".join(out[:limit]) + "."


def _hebrew_latin_hyphen(t: str) -> str:
    # "הrepository" -> "ה-repository"; standalone single prefix letter before Latin: "ב GitHub" -> "ב-GitHub"
    t = re.sub(rf"(?<![{HEB}])({PFX})(?=[A-Za-z])", r"\1-", t)
    # standalone prefix token before Latin: "ב GitHub", "שה build". ("של", "מה", "לה" are real words → untouched.)
    t = re.sub(rf"(?<![{HEB}\w])([{PREFIX}]|שה|וה|כשה|וב|ול|ומ)\s+(?=[A-Za-z0-9])", r"\1-", t)
    return t


def _spacing(t: str) -> str:
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)          # no space before punctuation
    t = re.sub(r"([,;:!?])(?=[^\s\d\"'\)])", r"\1 ", t)  # one space after (not inside numbers like 1,250)
    t = re.sub(r"([,.])\1+", r"\1", t)
    t = re.sub(r"^[,.\s]+", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _dedupe(t: str) -> str:
    def sub(m):
        return m.group(1) if m.group(1).lower() in STUTTER else m.group(0)
    return re.sub(rf"(?<![{HEB}\w])([{HEB}\w]+)(?:[\s,]+\1)+(?![{HEB}\w])", sub, t, flags=re.IGNORECASE)


def process(text: str, mode: str, dictionary: Dictionary | None) -> str:
    t = text.strip()
    if not t:
        return t
    if dictionary:
        t = dictionary.apply(t)
    t = _hebrew_latin_hyphen(t)
    if mode != "verbatim":
        t = _FILLER_RE.sub("", t)
        t = _dedupe(t)
    t = _spacing(t)
    if mode == "professional" and t:
        if t[0].isascii() and t[0].isalpha():
            t = t[0].upper() + t[1:]
        if t[-1] not in ".!?…:":
            t += "."
    return t
