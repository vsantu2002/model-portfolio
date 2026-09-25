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
            px[sym] = float(g.sort_values("entry_date")["entry_price"].iloc[-1])
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
        mtm = (px[r.symbol] - r.entry_price) * r.qty
        unreal += np.where(held, mtm, 0.0)
        invested += np.where(held, r.entry_price * r.qty, 0.0)
        if pd.notna(r.exit_date):
            pnl = (r.exit_price - r.entry_price) * r.qty - (r.commission or 0.0)
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
    op["value"] = op["ltp"] * op["qty"]
    op["pnl"] = op["value"] - op["cost"]
    op["pnl_pct"] = op["pnl"] / op["cost"] * 100
    op["weight_pct"] = op["value"] / equity_now * 100
    op["days"] = (today - op["entry_date"]).dt.days
    return op.sort_values("value", ascending=False)


def closed_trades(lots: pd.DataFrame) -> pd.DataFrame:
    cl = lots[~lots["is_open"]].copy()
    if cl.empty:
        return cl
    cl["pnl"] = (cl["exit_price"] - cl["entry_price"]) * cl["qty"] - cl["commission"]
    cl["pnl_pct"] = cl["pnl"] / cl["cost"] * 100
    cl["days"] = (cl["exit_date"] - cl["entry_date"]).dt.days
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
                       "pnl_pct": (r.exit_price / r.entry_price - 1) * 100, "comment": r.comment})
    df = pd.DataFrame(ev)
    if df.empty:
        return df
    df["week"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    return df.sort_values(["date", "action"], ascending=[False, True])


def contribution(lots: pd.DataFrame, last: dict) -> pd.DataFrame:
    df = lots.copy()
    mark = df["symbol"].map(last)
    df["pnl"] = np.where(df["is_open"], (mark - df["entry_price"]) * df["qty"],
                         (df["exit_price"] - df["entry_price"]) * df["qty"] - df["commission"])
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


def monthly_returns(eq: pd.DataFrame) -> pd.DataFrame:
    m = eq["equity"].resample("ME").last()
    prev = m.shift(1)
    prev.iloc[0] = eq["equity"].iloc[0]
    r = (m / prev - 1) * 100
    t = pd.DataFrame({"year": r.index.year, "month": r.index.strftime("%b"), "ret": r.values})
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    piv = t.pivot(index="year", columns="month", values="ret").reindex(columns=order)
    yearly = eq["equity"].resample("YE").last()
    ystart = yearly.shift(1)
    ystart.iloc[0] = eq["equity"].iloc[0]
    piv["Year"] = ((yearly / ystart - 1) * 100).values
    return piv


def equal_weight_qty(equity_now: float, slots: int, price: float) -> float:
    if not price or price <= 0 or not slots:
        return 0.0
    return float(math.floor(equity_now / slots / price))
