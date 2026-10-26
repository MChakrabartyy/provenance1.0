"""Data quality rules. Every standard row passes through these before it is stored.

Each rule returns None (pass) or a QualityFailure. The first failing rule wins; the row goes to
quarantine with the rule name and reason. Rules are pure functions over the row plus a context
object holding what they need (known instruments, already-seen trade keys, prior prices).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

from provenance.config import PRICE_JUMP_THRESHOLD, SOURCE_DISAGREEMENT_THRESHOLD
from provenance.models import PriceRow, QualityFailure, Trade


@dataclass
class QualityContext:
    known_instruments: dict[str, dict]                       # symbol -> instrument row
    existing_trade_keys: set[tuple[str, str]] = field(default_factory=set)   # from DB
    seen_trade_keys: set[tuple[str, str]] = field(default_factory=set)       # within this run
    prior_close: dict[tuple[str, str], Decimal] = field(default_factory=dict)  # (symbol, source) -> last good close
    price_jump_threshold: Decimal = Decimal(str(PRICE_JUMP_THRESHOLD))
    disagreement_threshold: Decimal = Decimal(str(SOURCE_DISAGREEMENT_THRESHOLD))


# ---------------- trade rules ----------------

def required_fields(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    missing = [n for n, v in (("symbol", t.symbol), ("qty", t.qty), ("price", t.price), ("time", t.trade_time)) if v in (None, "")]
    if missing:
        return QualityFailure("required_fields", f"missing {', '.join(missing)}")
    return None


def positive_price(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    if t.price is not None and t.price <= 0:
        return QualityFailure("positive_price", f"price {t.price} is not positive")
    return None


def option_sanity(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    if t.instrument_type != "option":
        return None
    as_of = t.trade_time.date() if t.trade_time else date.today()
    if t.expiry is not None and t.expiry < as_of:
        return QualityFailure("option_sanity", f"expiry {t.expiry} is before trade date {as_of}")
    if t.strike is not None and t.strike <= 0:
        return QualityFailure("option_sanity", f"strike {t.strike} is not positive")
    return None


def unknown_instrument(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    if t.symbol and t.symbol not in ctx.known_instruments:
        return QualityFailure("unknown_instrument", f"symbol {t.symbol} not in instruments")
    return None


def duplicate_trade(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    key = (t.manager_id, t.external_trade_id)
    if key in ctx.existing_trade_keys or key in ctx.seen_trade_keys:
        return QualityFailure("duplicate_trade", f"(manager_id, external_trade_id)=({t.manager_id}, {t.external_trade_id}) already exists")
    return None


TRADE_RULES: list[Callable[[Trade, QualityContext], QualityFailure | None]] = [
    required_fields, positive_price, option_sanity, unknown_instrument, duplicate_trade,
]


def check_trade(t: Trade, ctx: QualityContext) -> QualityFailure | None:
    for rule in TRADE_RULES:
        failure = rule(t, ctx)
        if failure:
            return failure
    ctx.seen_trade_keys.add((t.manager_id, t.external_trade_id))
    return None


# ---------------- price rules ----------------

def price_required(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    if p.close is None:
        return QualityFailure("required_fields", "missing close")
    return None


def price_positive(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    if p.close is not None and p.close <= 0:
        return QualityFailure("positive_price", f"close {p.close} is not positive")
    return None


def price_unknown_instrument(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    if p.symbol not in ctx.known_instruments:
        return QualityFailure("unknown_instrument", f"symbol {p.symbol} not in instruments")
    return None


def stale_price(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    price_date = p.raw.get("price_date")
    if price_date and str(price_date) < p.as_of_date.isoformat():
        return QualityFailure("stale_price", f"price dated {price_date} used for {p.as_of_date}")
    return None


def price_jump(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    """Stocks only: option premiums near the money legitimately move more than any sane stock threshold."""
    inst = ctx.known_instruments.get(p.symbol)
    if inst is not None and inst.get("type") == "option":
        return None
    prior = ctx.prior_close.get((p.symbol, p.source))
    if prior is None or prior == 0 or p.close is None:
        return None
    move = abs(p.close - prior) / prior
    if move > ctx.price_jump_threshold:
        return QualityFailure("price_jump", f"close moved {move:.1%} vs prior {prior} (threshold {ctx.price_jump_threshold:.0%})")
    return None


PRICE_RULES: list[Callable[[PriceRow, QualityContext], QualityFailure | None]] = [
    price_required, price_positive, price_unknown_instrument, stale_price, price_jump,
]


def check_price(p: PriceRow, ctx: QualityContext) -> QualityFailure | None:
    for rule in PRICE_RULES:
        failure = rule(p, ctx)
        if failure:
            return failure
    ctx.prior_close[(p.symbol, p.source)] = p.close  # type: ignore[assignment]
    return None


def source_disagreement(rows: list[PriceRow], ctx: QualityContext, primary: str) -> list[tuple[PriceRow, QualityFailure]]:
    """Cross-check sources for the same (symbol, date). Rows are the ones that passed the per-row rules.

    When the sources differ by more than the threshold the non-primary row is quarantined; the primary
    stays so the day can still be marked, and the disagreement is visible in the report.
    """
    by_key: dict[tuple[str, date], dict[str, PriceRow]] = {}
    for r in rows:
        by_key.setdefault((r.symbol, r.as_of_date), {})[r.source] = r
    failures: list[tuple[PriceRow, QualityFailure]] = []
    for (sym, d), srcs in by_key.items():
        if primary not in srcs or len(srcs) < 2:
            continue
        p = srcs[primary].close
        for name, other in srcs.items():
            if name == primary or other.close is None or p in (None, 0):
                continue
            diff = abs(other.close - p) / p
            if diff > ctx.disagreement_threshold:
                failures.append((other, QualityFailure("source_disagreement", f"{name} {other.close} vs {primary} {p} differ by {diff:.2%}")))
    return failures
