"""Seeded synthetic data generator for managers A to D.

Produces, under data/synthetic/generated/:
  manager_a/<date>.csv          Ticker,Qty,Px,Time  (US dates)
  manager_b/<date>.json         nested instrument objects with option fields
  manager_c/<date>.xlsx         header row on line 3, quantities like "1,200"
  manager_d/<date>.csv          different column names
  prices/<source>.csv           two sources for cross-checking
  rates.csv                     risk-free curve per day
  memos/*.md                    research memos, clearly labelled SYNTHETIC
  instruments.csv, managers.csv, users.csv
  planted.json                  the problems that were planted, for the eval harness

Planted problems (all must land in quarantine):
  * two stale price days for one stock
  * one price 100x too high in one source
  * a resent file with 12 duplicate trades
  * one option with an expiry in the past
  * one symbol not in the instruments table
Everything is derived from a fixed seed so reruns are byte-identical.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook

from provenance.config import DATA_DIR

SEED = 20261026
END_DATE = date(2026, 10, 23)
N_DAYS = 60

UNIVERSE = {
    # symbol: (sector, start_price)
    "NVDA": ("Semiconductors", 185), "AMD": ("Semiconductors", 160), "AVGO": ("Semiconductors", 290), "INTC": ("Semiconductors", 24),
    "AAPL": ("Technology", 232), "MSFT": ("Technology", 420), "GOOGL": ("Technology", 175), "META": ("Technology", 560), "CRM": ("Technology", 270),
    "ORCL": ("Technology", 150), "ADBE": ("Technology", 480), "NOW": ("Technology", 850), "SNOW": ("Technology", 120), "PLTR": ("Technology", 40),
    "AMZN": ("Consumer Discretionary", 190), "TSLA": ("Consumer Discretionary", 250), "HD": ("Consumer Discretionary", 380), "NKE": ("Consumer Discretionary", 80),
    "JNJ": ("Healthcare", 160), "PFE": ("Healthcare", 28), "MRK": ("Healthcare", 110), "LLY": ("Healthcare", 880), "ABBV": ("Healthcare", 180),
    "UNH": ("Healthcare", 560), "TMO": ("Healthcare", 560), "AMGN": ("Healthcare", 320), "GILD": ("Healthcare", 85), "VRTX": ("Healthcare", 470),
    "REGN": ("Healthcare", 1000), "BMY": ("Healthcare", 50), "ISRG": ("Healthcare", 480), "MDT": ("Healthcare", 88), "CVS": ("Healthcare", 60), "BIIB": ("Healthcare", 200),
    "JPM": ("Financials", 210), "GS": ("Financials", 500), "BAC": ("Financials", 42), "V": ("Financials", 290),
    "XOM": ("Energy", 115), "CVX": ("Energy", 155), "COP": ("Energy", 110),
    "CAT": ("Industrials", 360), "DE": ("Industrials", 400), "UNP": ("Industrials", 240), "GE": ("Industrials", 180),
    "PG": ("Consumer Staples", 170), "KO": ("Consumer Staples", 65), "WMT": ("Consumer Staples", 80), "COST": ("Consumer Staples", 900),
}

MANAGERS = [
    {"id": "A", "name": "Manager A", "pod": "Tech L/S", "strategy": "Tech long/short", "format": "csv"},
    {"id": "B", "name": "Manager B", "pod": "Vol", "strategy": "Volatility", "format": "json"},
    {"id": "C", "name": "Manager C", "pod": "Healthcare L/S", "strategy": "Healthcare long/short", "format": "xlsx"},
    {"id": "D", "name": "Manager D", "pod": "Generalist", "strategy": "Generalist", "format": "csv"},
]

BOOKS = {
    "A": ["NVDA", "AMD", "AVGO", "AAPL", "MSFT", "GOOGL", "META", "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "AMZN", "TSLA", "INTC"],
    "A_short": ["INTC", "SNOW", "PLTR", "ADBE"],
    "B": ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META"],
    "C": ["JNJ", "PFE", "MRK", "LLY", "ABBV", "UNH", "TMO", "AMGN", "GILD", "VRTX", "REGN", "BMY", "ISRG", "MDT", "CVS"],
    "C_short": ["PFE", "CVS", "BMY"],
    "D": ["AAPL", "AMZN", "JPM", "GS", "BAC", "V", "XOM", "CVX", "COP", "CAT", "DE", "UNP", "GE", "PG", "KO", "WMT", "COST", "LLY", "UNH", "MSFT", "GOOGL", "HD", "NKE", "TSLA", "META"],
}

USERS = [
    {"email": "pm.a@provenance.local", "role": "pm", "manager_id": "A", "token": "tok-pm-a"},
    {"email": "pm.b@provenance.local", "role": "pm", "manager_id": "B", "token": "tok-pm-b"},
    {"email": "pm.c@provenance.local", "role": "pm", "manager_id": "C", "token": "tok-pm-c"},
    {"email": "pm.d@provenance.local", "role": "pm", "manager_id": "D", "token": "tok-pm-d"},
    {"email": "risk@provenance.local", "role": "risk", "manager_id": "", "token": "tok-risk"},
    {"email": "eng@provenance.local", "role": "engineer", "manager_id": "", "token": "tok-eng"},
]


def trading_days(end: date, n: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def q2(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gen_prices(rng: random.Random, days: list[date]) -> dict[str, dict[date, Decimal]]:
    out: dict[str, dict[date, Decimal]] = {}
    for sym, (_, start) in UNIVERSE.items():
        px = float(start)
        series = {}
        for d in days:
            px *= 1 + rng.gauss(0.0004, 0.018)
            series[d] = q2(px)
        out[sym] = series
    return out


def option_symbol(und: str, expiry: date, cp: str, strike: Decimal) -> str:
    return f"{und}{expiry.strftime('%y%m%d')}{cp}{int(strike):08d}"


def main(out_dir: Path = DATA_DIR) -> dict:
    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    days = trading_days(END_DATE, N_DAYS)
    planted: dict = {"stale_price": [], "price_jump": [], "duplicate_trade": {}, "option_sanity": {}, "unknown_instrument": {}}

    # ---------- instruments ----------
    instruments: list[dict] = [
        {"symbol": s, "type": "stock", "underlying": "", "strike": "", "expiry": "", "call_put": "", "sector": sec}
        for s, (sec, _) in UNIVERSE.items()
    ]
    prices = gen_prices(rng, days)
    options: list[dict] = []
    future_exp = END_DATE + timedelta(days=56)  # ~8 weeks out
    for und in BOOKS["B"]:
        spot = prices[und][days[0]]
        for cp in ("C", "P"):
            strike = Decimal(int(float(spot) * (1.05 if cp == "C" else 0.95) / 5) * 5)
            sym = option_symbol(und, future_exp, cp, strike)
            opt = {"symbol": sym, "type": "option", "underlying": und, "strike": str(strike), "expiry": future_exp.isoformat(),
                   "call_put": "call" if cp == "C" else "put", "sector": UNIVERSE[und][0]}
            options.append(opt)
            instruments.append(opt)
    # Option prices: simple intrinsic + time value proxy, deterministic
    for opt in options:
        und = opt["underlying"]
        series = {}
        for d in days:
            s = float(prices[und][d]); k = float(opt["strike"])
            intrinsic = max(0.0, s - k) if opt["call_put"] == "call" else max(0.0, k - s)
            tv = 0.04 * s * max(0.1, (future_exp - d).days / 365) ** 0.5
            series[d] = q2(intrinsic + tv)
        prices[opt["symbol"]] = series

    with (out_dir / "instruments.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(instruments[0].keys())); w.writeheader(); w.writerows(instruments)
    with (out_dir / "managers.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "pod", "strategy", "onboarded_at"]); w.writeheader()
        for i, m in enumerate(MANAGERS):
            w.writerow({"id": m["id"], "name": m["name"], "pod": m["pod"], "strategy": m["strategy"], "onboarded_at": (days[0] - timedelta(days=30 * (4 - i))).isoformat()})
    with (out_dir / "users.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(USERS[0].keys())); w.writeheader(); w.writerows(USERS)

    # ---------- price files, two sources, with planted problems ----------
    pdir = out_dir / "prices"; pdir.mkdir(exist_ok=True)
    stale_sym, jump_sym = "ORCL", "GILD"
    stale_days = days[-6:-4]  # two consecutive days near the end
    jump_day = days[-3]
    planted["stale_price"] = [{"symbol": stale_sym, "as_of_date": d.isoformat()} for d in stale_days]
    planted["price_jump"] = [{"symbol": jump_sym, "as_of_date": jump_day.isoformat(), "source": "primary_synth"}]
    for source, noise in (("primary_synth", 0.0), ("secondary_synth", 0.002)):
        with (pdir / f"{source}.csv").open("w", newline="") as f:
            w = csv.writer(f); w.writerow(["symbol", "as_of_date", "price_date", "close", "source"])
            for sym, series in prices.items():
                for i, d in enumerate(days):
                    close = series[d]
                    price_date = d
                    if source == "secondary_synth":
                        close = q2(float(close) * (1 + rng.gauss(0, noise)))
                    if source == "primary_synth" and sym == stale_sym and d in stale_days:
                        price_date = stale_days[0] - timedelta(days=1)
                        close = series[days[i - (1 if d == stale_days[0] else 2)]]
                    if source == "primary_synth" and sym == jump_sym and d == jump_day:
                        close = close * 100
                    w.writerow([sym, d.isoformat(), price_date.isoformat(), str(close), source])

    with (out_dir / "rates.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["as_of_date", "tenor", "rate"])
        for d in days:
            for tenor, base in (("1M", 0.0415), ("3M", 0.0405), ("1Y", 0.0380)):
                w.writerow([d.isoformat(), tenor, f"{base + rng.gauss(0, 0.0003):.4f}"])

    # ---------- trades ----------
    def gen_trades(mid: str, syms: list[str], shorts: list[str], base_qty: int, per_day: int):
        rows = []
        tid = 0
        for i, d in enumerate(days):
            todays = []
            if i == 0:
                for s in syms:
                    sign = -1 if s in shorts else 1
                    tid += 1
                    todays.append((s, sign * base_qty * rng.randint(2, 6), prices[s][d], f"{mid}-{tid:05d}"))
            for _ in range(rng.randint(0, per_day)):
                s = rng.choice(syms); tid += 1
                todays.append((s, rng.choice([-1, 1]) * base_qty * rng.randint(1, 3), prices[s][d] * Decimal(str(1 + rng.gauss(0, 0.003))), f"{mid}-{tid:05d}"))
            for s, qty, px, ext in todays:
                hh, mm = rng.randint(9, 15), rng.randint(0, 59)
                rows.append({"symbol": s, "qty": qty, "price": q2(float(px)), "time": datetime(d.year, d.month, d.day, hh, mm), "ext": ext, "date": d})
        return rows

    # Manager A: CSV, Ticker,Qty,Px,Time (US date)
    adir = out_dir / "manager_a"; adir.mkdir(exist_ok=True)
    a_rows = gen_trades("A", BOOKS["A"], BOOKS["A_short"], 100, 3)
    by_day: dict[date, list] = {}
    for r in a_rows: by_day.setdefault(r["date"], []).append(r)
    for d, rows in by_day.items():
        with (adir / f"trades_{d.isoformat()}.csv").open("w", newline="") as f:
            w = csv.writer(f); w.writerow(["Ticker", "Qty", "Px", "Time", "TradeID"])
            for r in rows:
                w.writerow([r["symbol"], r["qty"], str(r["price"]), r["time"].strftime("%m/%d/%Y %H:%M"), r["ext"]])
    # planted: resent file with 12 duplicate trades (same TradeIDs as an earlier day) + a few new ones
    dup_day = days[0]  # the initial-positions day always has >= 12 rows
    dup_rows = by_day[dup_day][:12]
    resend = adir / f"trades_{days[-2].isoformat()}_resend.csv"
    with resend.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["Ticker", "Qty", "Px", "Time", "TradeID"])
        for r in dup_rows:
            w.writerow([r["symbol"], r["qty"], str(r["price"]), r["time"].strftime("%m/%d/%Y %H:%M"), r["ext"]])
    planted["duplicate_trade"] = {"manager_id": "A", "file": resend.name, "count": 12, "original_day": dup_day.isoformat()}

    # Manager B: JSON nested, options
    bdir = out_dir / "manager_b"; bdir.mkdir(exist_ok=True)
    opt_syms = [o["symbol"] for o in options]
    b_rows = gen_trades("B", opt_syms, [], 10, 2)
    opt_by_sym = {o["symbol"]: o for o in options}
    by_day = {}
    for r in b_rows: by_day.setdefault(r["date"], []).append(r)
    expired = {"symbol": option_symbol("AAPL", days[5], "C", Decimal(200)), "underlying": "AAPL", "strike": "200", "expiry": days[5].isoformat(), "call_put": "call"}
    for d, rows in by_day.items():
        payload = []
        for r in rows:
            o = opt_by_sym[r["symbol"]]
            payload.append({"trade_id": r["ext"], "executed_at": r["time"].isoformat(), "quantity": r["qty"], "fill_price": str(r["price"]),
                            "instrument": {"kind": "option", "underlying": o["underlying"], "strike": float(o["strike"]), "expiry": o["expiry"], "right": o["call_put"].upper()}})
        if d == days[-4]:  # planted: expired option
            payload.append({"trade_id": "B-EXPIRED-1", "executed_at": datetime(d.year, d.month, d.day, 10, 5).isoformat(), "quantity": 10, "fill_price": "3.10",
                            "instrument": {"kind": "option", "underlying": "AAPL", "strike": 200.0, "expiry": expired["expiry"], "right": "CALL"}})
            planted["option_sanity"] = {"manager_id": "B", "trade_id": "B-EXPIRED-1", "expiry": expired["expiry"], "date": d.isoformat()}
        (bdir / f"fills_{d.isoformat()}.json").write_text(json.dumps({"manager": "B", "as_of": d.isoformat(), "fills": payload}, indent=1))

    # Manager C: Excel, header on row 3, qty strings like "1,200"
    cdir = out_dir / "manager_c"; cdir.mkdir(exist_ok=True)
    c_rows = gen_trades("C", BOOKS["C"], BOOKS["C_short"], 400, 3)
    by_day = {}
    for r in c_rows: by_day.setdefault(r["date"], []).append(r)
    for d, rows in by_day.items():
        wb = Workbook(); ws = wb.active; ws.title = "Blotter"
        ws.append(["Manager C daily blotter"]); ws.append([f"As of {d.isoformat()}"])
        ws.append(["Security", "Side", "Quantity", "Price", "Executed", "Ref"])
        for r in rows:
            ws.append([r["symbol"], "SELL" if r["qty"] < 0 else "BUY", f"{abs(r['qty']):,}", float(r["price"]), r["time"].strftime("%Y-%m-%d %H:%M"), r["ext"]])
        wb.save(cdir / f"blotter_{d.isoformat()}.xlsx")

    # Manager D: CSV with different column names + planted unknown symbol
    ddir = out_dir / "manager_d"; ddir.mkdir(exist_ok=True)
    d_rows = gen_trades("D", BOOKS["D"], [], 200, 4)
    by_day = {}
    for r in d_rows: by_day.setdefault(r["date"], []).append(r)
    for d, rows in by_day.items():
        with (ddir / f"activity_{d.isoformat()}.csv").open("w", newline="") as f:
            w = csv.writer(f); w.writerow(["symbol", "shares", "exec_price", "exec_ts", "order_ref"])
            for r in rows:
                w.writerow([r["symbol"], r["qty"], str(r["price"]), r["time"].isoformat(), r["ext"]])
            if d == days[-5]:
                w.writerow(["ZZZQ", 500, "12.34", datetime(d.year, d.month, d.day, 11, 0).isoformat(), "D-UNKNOWN-1"])
                planted["unknown_instrument"] = {"manager_id": "D", "symbol": "ZZZQ", "trade_id": "D-UNKNOWN-1", "date": d.isoformat()}

    # ---------- memos (synthetic, template based) ----------
    mdir = out_dir / "memos"; mdir.mkdir(exist_ok=True)
    memo_topics = {
        "A": [("NVDA", "Data-center demand stays ahead of supply; we expect pricing power to hold through the next two quarters."),
              ("INTC", "We remain short INTC on foundry execution risk and share loss in servers."),
              ("SNOW", "Consumption growth is decelerating; short thesis intact until net revenue retention stabilises."),
              ("META", "Ad load and Reels monetisation support our long; capex guide is the main risk.")],
        "B": [("AAPL", "Implied vol in AAPL screens cheap into the product cycle; we own calls spread against puts."),
              ("TSLA", "Skew is extreme; we are long downside puts funded by upside calls."),
              ("NVDA", "Earnings straddle looks underpriced versus realised moves over the past four prints.")],
        "C": [("LLY", "GLP-1 supply expansion supports the long; we trimmed into strength."),
              ("PFE", "Short PFE: post-COVID revenue cliff and thin late-stage pipeline."),
              ("VRTX", "Pain franchise optionality is not in consensus numbers."),
              ("CVS", "Short on Medicare Advantage margin pressure and Oak Street integration risk."),
              ("GILD", "Oncology pipeline underappreciated; long with a stop below 200-day.")],
        "D": [("XOM", "Energy as an inflation hedge; overweight versus the benchmark."),
              ("JPM", "Net interest income resilience keeps us long money-center banks."),
              ("COST", "Membership fee hike is a clean earnings tailwind.")],
    }
    memo_dates = [days[10], days[25], days[40], days[52], days[57]]
    for mid, items in memo_topics.items():
        for i, (sym, thesis) in enumerate(items):
            d = memo_dates[i]
            text = (f"# SYNTHETIC research memo: {sym} ({UNIVERSE[sym][0]})\n\n"
                    f"Manager {mid} | {d.isoformat()} | This memo is synthetic test data generated for the Provenance project. It is not investment research.\n\n"
                    f"## Thesis\n\n{thesis}\n\n"
                    f"## Positioning\n\nWe {'are short' if sym in BOOKS.get(mid + '_short', []) else 'hold a long position in'} {sym}. Reference close at writing: {prices[sym][d]}.\n\n"
                    f"## Risks\n\nKey risks include a guidance miss, multiple compression across {UNIVERSE[sym][0]}, and macro-driven factor rotation. "
                    f"We would reassess if the position moves more than 15% against us.\n")
            (mdir / f"manager_{mid.lower()}_{sym}_{d.isoformat()}.md").write_text(text)
    # Firm-wide filing stand-ins (synthetic excerpts; the real pipeline pulls EDGAR)
    filings = {
        "NVDA": "Supply constraints: We depend on a limited number of foundry and memory partners. Demand for our data center products has exceeded supply, and we expect supply to remain constrained for several quarters. Lead times for advanced packaging remain extended.",
        "AAPL": "We rely on single-source suppliers for certain components. Shortages or delays could affect our ability to meet demand for new products.",
        "LLY": "Demand for incretin medicines has outpaced our manufacturing capacity; we are investing in new sites to expand supply.",
    }
    for sym, body in filings.items():
        (mdir / f"filing_{sym}_10K_synthetic.md").write_text(
            f"# SYNTHETIC 10-K excerpt: {sym}\n\nFirm-wide document. Synthetic stand-in for an SEC EDGAR filing used in the Provenance v0 test data.\n\n## Risk Factors\n\n{body}\n")

    (out_dir / "planted.json").write_text(json.dumps(planted, indent=2))
    (out_dir / "calendar.json").write_text(json.dumps([d.isoformat() for d in days]))
    return planted


if __name__ == "__main__":
    p = main()
    print(json.dumps(p, indent=2))
