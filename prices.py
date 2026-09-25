"""
prices.py -- end-of-day closes from yfinance.

Symbols are stored as yfinance tickers (e.g. HFCL.NS, 543210.BO, AAPL). For an
Indian symbol with no data on NSE (.NS) the BSE listing (.BO) is tried
automatically -- covers BSE-only small caps entered with the wrong suffix.

Closes are unadjusted for dividends (auto_adjust=False) so they line up with
the prices actually paid. If Yahoo returns nothing for a stock, the app falls
back to that lot's manual price (editable in the UI), then its entry price.

PRICE_SOURCE=mock (env var) swaps in a deterministic random walk -- only for
testing the app where Yahoo is unreachable.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd


def _field(raw: pd.DataFrame, name: str, tickers: list[str]) -> pd.DataFrame:
    if name not in raw.columns.get_level_values(0):
        return pd.DataFrame(index=raw.index)
    f = raw[name] if isinstance(raw.columns, pd.MultiIndex) else raw[[name]]
    if not isinstance(f, pd.DataFrame):
        f = f.to_frame()
    if list(f.columns) == [name]:
        f.columns = tickers[:1]
    return f


def _download(tickers: list[str], start: str):
    """Returns (closes, splits). Closes are Yahoo's split-adjusted closes;
    splits holds each split/bonus ratio on its ex-date (0 = none)."""
    import yfinance as yf
    if not tickers:
        return pd.DataFrame(), pd.DataFrame()
    raw = yf.download(tickers, start=start, auto_adjust=False, actions=True, progress=False,
                      group_by="column", threads=True)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=tickers), pd.DataFrame()
    close = _field(raw, "Close", tickers).dropna(how="all")
    splits = _field(raw, "Stock Splits", tickers).fillna(0.0)
    splits = splits.loc[:, (splits > 0).any()] if not splits.empty else splits
    return close, splits


def _alternate(sym: str):
    if sym.endswith(".NS"):
        return sym[:-3] + ".BO"
    if sym.endswith(".BO"):
        return sym[:-3] + ".NS"
    return None


def _mock(tickers: list[str], start: str, seed_prices: dict):
    """Random walk per ticker. MOCK_SPLITS="SYM.NS:2026-01-15:10,..." adds a
    split, with history before it divided by the ratio, as Yahoo does."""
    idx = pd.bdate_range(start, pd.Timestamp.today().normalize())
    out = {}
    for t in tickers:
        rng = np.random.default_rng(sum(map(ord, t)))
        base = seed_prices.get(t, 1000.0)
        out[t] = base * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(idx))))
    closes = pd.DataFrame(out, index=idx)
    splits = pd.DataFrame(0.0, index=idx, columns=[])
    for item in filter(None, os.environ.get("MOCK_SPLITS", "").split(",")):
        sym, d, ratio = item.split(":")
        d, ratio = pd.Timestamp(d), float(ratio)
        if sym in closes:
            # Yahoo's history is continuous on today's share basis: every day,
            # before and after the split, is the real price / ratio of shares.
            closes[sym] = closes[sym] / ratio
            splits[sym] = 0.0
            splits.loc[splits.index[splits.index.searchsorted(d)], sym] = ratio
    return closes, splits


def fetch_closes(tickers: list[str], start: str, seed_prices: dict | None = None):
    """Returns (closes, splits, missing): one column per requested ticker."""
    tickers = sorted(set(t for t in tickers if t))
    if os.environ.get("PRICE_SOURCE") == "mock":
        closes, splits = _mock(tickers, start, seed_prices or {})
        return closes, splits, []
    closes, splits = _download(tickers, start)
    missing = [t for t in tickers if t not in closes.columns or closes[t].dropna().empty]
    retry = {t: _alternate(t) for t in missing if _alternate(t)}
    if retry:
        alt, alt_splits = _download(list(retry.values()), start)
        for orig, a in retry.items():
            if a in alt.columns and not alt[a].dropna().empty:
                closes[orig] = alt[a]
                if a in alt_splits.columns:
                    splits[orig] = alt_splits[a]
    missing = [t for t in tickers if t not in closes.columns or closes[t].dropna().empty]
    return closes, splits.fillna(0.0), missing
