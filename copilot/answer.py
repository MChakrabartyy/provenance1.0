"""Answer schema and guardrails: the number check, required warnings, and refusal detection."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field


class Figure(BaseModel):
    label: str
    value: str
    source_rows: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    document_id: int
    chunk_index: int
    title: str | None = None


class Answer(BaseModel):
    answer: str
    figures: list[Figure] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: Literal["answered", "partial", "refused"]


ANSWER_JSON_SCHEMA = Answer.model_json_schema()

PREDICTION_RE = re.compile(r"\b(will|going to|predict|forecast|tomorrow|next (week|month|year|quarter)|should i (buy|sell|hold)|price target|outlook for|recommend(ation)?|advice|is .* a (good|bad) (buy|investment))\b", re.I)

# numbers we never require in figures: years and ISO dates, 10-K/10-Q, small counts (top 5, 3 positions)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FILING_RE = re.compile(r"\b10-[KQ]\b")
_NUM_RE = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?![\w-])")


def _canon(s: str) -> Decimal | None:
    s = s.strip().replace("$", "").replace(",", "").replace("%", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[str]:
    text = _DATE_RE.sub(" ", text)
    text = _FILING_RE.sub(" ", text)
    text = re.sub(r"\bManager [A-Z]\b", " ", text)
    text = re.sub(r"\b[A-Z]{1,5}\d{6}[CP]\d{8}\b", " ", text)  # option symbols
    return [m.group(0) for m in _NUM_RE.finditer(text)]


def number_check(ans: Answer) -> list[str]:
    """Return the numbers in `answer` that are not backed by a figure with source rows."""
    backed: set[Decimal] = set()
    for f in ans.figures:
        if not f.source_rows:
            continue
        for tok in numbers_in(f.value):
            d = _canon(tok)
            if d is not None:
                backed.add(d)
                for places in ("1", "0.1", "0.01"):
                    backed.add(d.quantize(Decimal(places)))
    unbacked = []
    for tok in numbers_in(ans.answer):
        d = _canon(tok)
        if d is None:
            continue
        if d == d.to_integral_value() and 0 <= d <= 20:
            continue  # counts like "top 5"
        if d not in backed:
            unbacked.append(tok)
    return unbacked


def is_prediction_request(q: str) -> bool:
    return bool(PREDICTION_RE.search(q))


def refusal(text: str) -> Answer:
    return Answer(answer=text, confidence="refused")
