"""
core.py -- data model and portfolio analytics (pure pandas, no Streamlit).

Data model (data/portfolio.json):
{
  "portfolios": {
    "<id>": {"name", "currency", "capital", "slots", "benchmarks": {label: ticker},
             "start_date" (optional, defaults to first entry)}
  },
  "lots": [
    {"id", "portfolio", "symbol", "name", "entry_date", "entry_price", "qty",
     "exit_date", "exit_price", "commission", "manual_price", "comment"}
  ]
}
A "lot" is one position from entry to exit. Re-entering a stock later is a new lot.
Open lots have exit_date = null. Dates are ISO strings (YYYY-MM-DD).

Accounting is fixed-capital, the same way the old portal computed Profit:
  equity(t) = capital + realised P&L of lots closed on/before t
                      + mark-to-market P&L of lots open at t
so no separate cash ledger is needed.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime

import numpy as np
import pandas as pd

LOT_FIELDS = ["id", "portfolio", "symbol", "name", "entry_date", "entry_price", "qty",
              "exit_date", "exit_price", "commission", "manual_price", "comment"]


def new_lot_id() -> str:
    return uuid.uuid4().hex[:10]


def empty_data() -> dict:
    return {"portfolios": {}, "lots": []}


def lots_frame(data: dict, portfolio: str) -> pd.DataFrame:
    rows = [l for l in data.get("lots", []) if l.get("portfolio") == portfolio]
    df = pd.DataFrame(rows, columns=LOT_FIELDS)
    if df.empty:
        return df
    for c in ("entry_date", "exit_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("entry_price", "qty", "exit_price", "commission", "manual_price"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["commission"] = df["commission"].fillna(0.0)
    df["comment"] = df["comment"].fillna("")
    df["name"] = df["name"].fillna("")
    df["is_open"] = df["exit_date"].isna()
    df["cost"] = df["entry_price"] * df["qty"]
    # Calculation basis (see apply_split_basis): identical to the recorded
    # values unless a split/bonus has to be reconciled.
    df["e_px"], df["q"], df["x_px"] = df["entry_price"], df["qty"], df["exit_price"]
    df["split_adj"] = False
    return df


def frame_to_lots(df: pd.DataFrame, portfolio: str) -> list[dict]:
    """Inverse of lots_frame, used after editing in the UI."""
    out = []
    for r in df.to_dict("records"):
        if not str(r.get("symbol") or "").strip():
            continue
        lot = {}
        for f in LOT_FIELDS:
            v = r.get(f)
            if isinstance(v, (pd.Timestamp, datetime, date)):
                v = pd.Timestamp(v).strftime("%Y-%m-%d") if not pd.isna(v) else None
            elif isinstance(v, float) and math.isnan(v):
                v = None
            elif v is pd.NaT:
                v = None
            lot[f] = v
        lot["id"] = lot.get("id") or new_lot_id()
        lot["portfolio"] = portfolio
        lot["symbol"] = str(lot["symbol"]).strip().upper()
        lot["commission"] = lot.get("commission") or 0.0
        out.append(lot)
    return out


# ── splits & bonuses ───────────────────────────────────────────────────────
def _split_factor(splits: pd.Series | None, when) -> float:
    """Product of all split ratios strictly after `when` (1.0 if none)."""
    if splits is None or splits.empty:
        return 1.0
    s = splits[(splits.index > when) & (splits > 0)]
    return float(s.prod()) if len(s) else 1.0


def apply_split_basis(lots: pd.DataFrame, closes: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Yahoo rewrites a stock's past prices after every split or bonus (a 1:1
    bonus is a 2:1 split), so its history is always on today's share basis.
    Recorded lots can be on either basis: entered before the split at the real
    price of the day, or already adjusted (as the old portal did for positions
    open at the time of a split). For each lot touched by a later split, compare
    its entry price with that day's close on both bases and take the closer one.
    Lots recorded at the real price are converted to today's basis:
    price / F, quantity x F, where F = product of splits after that date. Cost is
    unchanged, so P&L stays in real rupees and shares received in a split are
    counted correctly."""
    if lots.empty or splits is None or splits.empty:
        return lots
    out = lots.copy()
    cl = closes.copy()
    cl.index = pd.to_datetime(cl.index).tz_localize(None).normalize()
    sp = splits.copy()
    sp.index = pd.to_datetime(sp.index).tz_localize(None).normalize()
    for i, r in out.iterrows():
        if r.symbol not in sp.columns or r.symbol not in cl.columns:
            continue
        f_in = _split_factor(sp[r.symbol], r.entry_date)
        if f_in == 1.0:
            continue
        adj = cl[r.symbol].dropna().asof(r.entry_date)
        if adj is None or pd.isna(adj) or adj <= 0 or not r.entry_price:
            continue
        real = adj * f_in
        if abs(math.log(r.entry_price / real)) < abs(math.log(r.entry_price / adj)):
            out.at[i, "e_px"] = r.entry_price / f_in
            out.at[i, "q"] = r.qty * f_in
            if pd.notna(r.exit_date) and pd.notna(r.exit_price):
                out.at[i, "x_px"] = r.exit_price / _split_factor(sp[r.symbol], r.exit_date)
            out.at[i, "split_adj"] = True
    return out


# ── prices ─────────────────────────────────────────────────────────────────
def price_matrix(lots: pd.DataFrame, closes: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Daily close per symbol on a business-day index from first entry to `end`.
    Gaps are forward-filled. A symbol with no market data at all is held at its
    entry price, except the final day uses manual_price if one was entered."""
    start = lots["entry_date"].min()
    idx = pd.bdate_range(start, end)
    closes = closes.copy() if closes is not None else pd.DataFrame()
    if not closes.empty:
        closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
        closes = closes[~closes.index.duplicated(keep="last")]
    px = closes.reindex(idx.union(closes.index)).sort_index().ffill().reindex(idx) if not closes.empty \
        else pd.DataFrame(index=idx)
    for sym, g in lots.groupby("symbol"):
        manual = g["manual_price"].dropna()
        has_data = sym in px.columns and px[sym].notna().any()
        if not has_data:
            px[sym] = float(g.sort_values("entry_date")["e_px"].iloc[-1])
        else:
            px[sym] = px[sym].bfill()
        if len(manual):
            px.loc[px.index[-1], sym] = float(manual.iloc[-1])
    return px


def latest_prices(lots: pd.DataFrame, px: pd.DataFrame) -> dict:
    return {s: float(px[s].iloc[-1]) for s in lots["symbol"].unique() if s in px.columns}


# ── equity curve ──────────────────────────────────────────────────────────
def equity_curve(lots: pd.DataFrame, px: pd.DataFrame, capital: float) -> pd.DataFrame:
    idx = px.index
    realised = pd.Series(0.0, index=idx)
    unreal = pd.Series(0.0, index=idx)
    invested = pd.Series(0.0, index=idx)
    for r in lots.itertuples():
        held = (idx >= r.entry_date) & ((idx < r.exit_date) if pd.notna(r.exit_date) else True)
        mtm = (px[r.symbol] - r.e_px) * r.q
        unreal += np.where(held, mtm, 0.0)
        invested += np.where(held, r.e_px * r.q, 0.0)
        if pd.notna(r.exit_date):
            pnl = (r.x_px - r.e_px) * r.q - (r.commission or 0.0)
            realised += np.where(idx >= r.exit_date, pnl, 0.0)
    eq = capital + realised + unreal
    out = pd.DataFrame({"equity": eq, "realised": realised, "unrealised": unreal, "invested": invested})
    out["return_pct"] = (out["equity"] / capital - 1) * 100
    out["drawdown_pct"] = (out["equity"] / out["equity"].cummax() - 1) * 100
    out["exposure_pct"] = out["invested"] / out["equity"] * 100
    return out


def benchmark_curves(bench_closes: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Each benchmark rebased to 0% return on the portfolio's first day."""
    if bench_closes is None or bench_closes.empty:
        return pd.DataFrame(index=idx)
    b = bench_closes.copy()
    b.index = pd.to_datetime(b.index).tz_localize(None).normalize()
    b = b[~b.index.duplicated(keep="last")]
    b = b.reindex(idx.union(b.index)).sort_index().ffill().reindex(idx).bfill()
    return (b / b.iloc[0] - 1) * 100


# ── holdings & trades ─────────────────────────────────────────────────────
def holdings_table(lots: pd.DataFrame, last: dict, equity_now: float) -> pd.DataFrame:
    op = lots[lots["is_open"]].copy()
    if op.empty:
        return op
    today = pd.Timestamp.today().normalize()
    op["ltp"] = op["symbol"].map(last)
    op["entry_price"], op["qty"] = op["e_px"], op["q"]   # today's share basis
    op["value"] = op["ltp"] * op["qty"]
    op["pnl"] = op["value"] - op["cost"]
    op["pnl_pct"] = op["pnl"] / op["cost"] * 100
    op["weight_pct"] = op["value"] / equity_now * 100
    op["days"] = (today - op["entry_date"]).dt.days
    return op.sort_values("value", ascending=False)


def closed_trades(lots: pd.DataFrame, last: dict | None = None, priced: set | None = None) -> pd.DataFrame:
    """Recorded entry/exit shown as entered; P&L on the split-reconciled basis.
    ltp / since_exit_pct: what the stock did after the exit (both on today's
    share basis, so a later split doesn't read as a crash)."""
    cl = lots[~lots["is_open"]].copy()
    if cl.empty:
        return cl
    cl["pnl"] = (cl["x_px"] - cl["e_px"]) * cl["q"] - cl["commission"]
    cl["pnl_pct"] = cl["pnl"] / cl["cost"] * 100
    cl["days"] = (cl["exit_date"] - cl["entry_date"]).dt.days
    last, priced = last or {}, priced if priced is not None else set(last or {})
    cl["ltp"] = [last.get(sym) if sym in priced else np.nan for sym in cl["symbol"]]
    cl["since_exit_pct"] = (cl["ltp"] / cl["x_px"] - 1) * 100
    return cl.sort_values("exit_date", ascending=False)


def weekly_changes(lots: pd.DataFrame) -> pd.DataFrame:
    """One row per entry and per exit, tagged with the week (Mon) it fell in."""
    ev = []
    for r in lots.itertuples():
        ev.append({"date": r.entry_date, "action": "Entry", "symbol": r.symbol, "name": r.name,
                   "price": r.entry_price, "qty": r.qty, "pnl_pct": np.nan, "comment": r.comment})
        if pd.notna(r.exit_date):
            ev.append({"date": r.exit_date, "action": "Exit", "symbol": r.symbol, "name": r.name,
                       "price": r.exit_price, "qty": r.qty,
                       "pnl_pct": (r.x_px / r.e_px - 1) * 100, "comment": r.comment})
    df = pd.DataFrame(ev)
    if df.empty:
        return df
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    return df.sort_values(["date", "action"], ascending=[False, True])


def contribution(lots: pd.DataFrame, last: dict) -> pd.DataFrame:
    df = lots.copy()
    mark = df["symbol"].map(last)
    df["pnl"] = np.where(df["is_open"], (mark - df["e_px"]) * df["q"],
                         (df["x_px"] - df["e_px"]) * df["q"] - df["commission"])
    g = df.groupby("symbol").agg(name=("name", "last"), pnl=("pnl", "sum"), lots=("id", "count"),
                                 open=("is_open", "any"))
    return g.sort_values("pnl", ascending=False)


# ── statistics ───────────────────────────────────────────────────────────
def stats(eq: pd.DataFrame, closed: pd.DataFrame, bench: pd.DataFrame, capital: float) -> dict:
    s = {}
    first, last = eq.index[0], eq.index[-1]
    years = max((last - first).days, 1) / 365.25
    s["equity"] = float(eq["equity"].iloc[-1])
    s["total_pnl"] = s["equity"] - capital
    s["total_return_pct"] = float(eq["return_pct"].iloc[-1])
    s["cagr_pct"] = ((s["equity"] / capital) ** (1 / years) - 1) * 100 if years >= 0.25 else None
    s["max_dd_pct"] = float(eq["drawdown_pct"].min())
    s["current_dd_pct"] = float(eq["drawdown_pct"].iloc[-1])
    s["realised"] = float(eq["realised"].iloc[-1])
    s["unrealised"] = float(eq["unrealised"].iloc[-1])
    s["exposure_pct"] = float(eq["exposure_pct"].iloc[-1])
    s["since"] = first
    s["years"] = years
    if not closed.empty:
        w, l = closed[closed["pnl"] > 0], closed[closed["pnl"] <= 0]
        s["n_closed"] = len(closed)
        s["win_rate_pct"] = len(w) / len(closed) * 100
        s["avg_win_pct"] = float(w["pnl_pct"].mean()) if len(w) else None
        s["avg_loss_pct"] = float(l["pnl_pct"].mean()) if len(l) else None
        s["profit_factor"] = float(w["pnl"].sum() / -l["pnl"].sum()) if len(l) and l["pnl"].sum() < 0 else None
        s["avg_days_win"] = float(w["days"].mean()) if len(w) else None
        s["avg_days_loss"] = float(l["days"].mean()) if len(l) else None
        best, worst = closed.loc[closed["pnl_pct"].idxmax()], closed.loc[closed["pnl_pct"].idxmin()]
        s["best"] = (best["symbol"], float(best["pnl_pct"]))
        s["worst"] = (worst["symbol"], float(worst["pnl_pct"]))
    s["bench"] = {c: float(bench[c].iloc[-1]) for c in bench.columns if bench[c].notna().any()}
    return s


def monthly_returns(eq: pd.DataFrame, capital: float) -> pd.DataFrame:
    """Month-on-month change in portfolio value. The first month starts from
    the starting capital, and "Year" compounds the months (year-end value /
    previous year-end value), so the months chain exactly to the Overview's
    total return."""
    m = eq["equity"].resample("ME").last()
    prev = m.shift(1)
    prev.iloc[0] = capital
    r = (m / prev - 1) * 100
    t = pd.DataFrame({"year": r.index.year, "month": r.index.strftime("%b"), "ret": r.values})
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    piv = t.pivot(index="year", columns="month", values="ret").reindex(columns=order)
    yearly = eq["equity"].resample("YE").last()
    ystart = yearly.shift(1)
    ystart.iloc[0] = capital
    piv["Year"] = ((yearly / ystart - 1) * 100).values
    return piv


def equal_weight_qty(equity_now: float, slots: int, price: float) -> float:
    if not price or price <= 0 or not slots:
        return 0.0
    return float(math.floor(equity_now / slots / price))
