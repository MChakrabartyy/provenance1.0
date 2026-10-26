"""Black-Scholes Greeks. Float math internally (transcendentals), results quantized to Decimal."""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from provenance.engine.types import Result

Q6 = Decimal("0.000001")


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _n(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def greeks(call_put: str, spot: Decimal, strike: Decimal, expiry: date, as_of: date, rate: Decimal, vol: Decimal) -> Result:
    """Return delta, gamma, vega (per 1.00 of vol), theta (per calendar day), price. Per share, no multiplier."""
    T = max((expiry - as_of).days, 0) / 365.0
    S, K, r, sigma = float(spot), float(strike), float(rate), float(vol)
    is_call = call_put.lower().startswith("c")
    if T == 0 or sigma == 0:
        intrinsic = max(0.0, S - K) if is_call else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
        data = {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "price": intrinsic}
    else:
        sq = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sq)
        d2 = d1 - sigma * sq
        disc = math.exp(-r * T)
        if is_call:
            price = S * _N(d1) - K * disc * _N(d2)
            delta = _N(d1)
            theta = (-S * _n(d1) * sigma / (2 * sq) - r * K * disc * _N(d2)) / 365.0
        else:
            price = K * disc * _N(-d2) - S * _N(-d1)
            delta = _N(d1) - 1.0
            theta = (-S * _n(d1) * sigma / (2 * sq) + r * K * disc * _N(-d2)) / 365.0
        gamma = _n(d1) / (S * sigma * sq)
        vega = S * _n(d1) * sq
        data = {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "price": price}
    out = {k: Decimal(str(v)).quantize(Q6) for k, v in data.items()}
    out.update({"spot": spot, "strike": strike, "expiry": expiry.isoformat(), "as_of": as_of.isoformat(), "rate": rate, "vol": vol, "call_put": "call" if is_call else "put"})
    return Result(data=out, source_rows=[])
