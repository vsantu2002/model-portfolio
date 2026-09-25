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


def _download(tickers: list[str], start: str) -> pd.DataFrame:
    import yfinance as yf
    if not tickers:
        return pd.DataFrame()
    raw = yf.download(tickers, start=start, auto_adjust=False, progress=False,
                      group_by="column", threads=True)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=tickers)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if not isinstance(close, pd.DataFrame):
        close = close.to_frame()
    if list(close.columns) == ["Close"]:
        close.columns = tickers[:1]
    return close.dropna(how="all")


def _alternate(sym: str):
    if sym.endswith(".NS"):
        return sym[:-3] + ".BO"
    if sym.endswith(".BO"):
        return sym[:-3] + ".NS"
    return None


def _mock(tickers: list[str], start: str, seed_prices: dict) -> pd.DataFrame:
    idx = pd.bdate_range(start, pd.Timestamp.today().normalize())
    out = {}
    for t in tickers:
        rng = np.random.default_rng(abs(hash(t)) % (2**32))
        base = seed_prices.get(t, 1000.0)
        out[t] = base * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(idx))))
    return pd.DataFrame(out, index=idx)


def fetch_closes(tickers: list[str], start: str, seed_prices: dict | None = None):
    """Returns (closes DataFrame with one column per requested ticker, missing list)."""
    tickers = sorted(set(t for t in tickers if t))
    if os.environ.get("PRICE_SOURCE") == "mock":
        return _mock(tickers, start, seed_prices or {}), []
    closes = _download(tickers, start)
    missing = [t for t in tickers if t not in closes.columns or closes[t].dropna().empty]
    retry = {t: _alternate(t) for t in missing if _alternate(t)}
    if retry:
        alt = _download(list(retry.values()), start)
        for orig, a in retry.items():
            if a in alt.columns and not alt[a].dropna().empty:
                closes[orig] = alt[a]
    missing = [t for t in tickers if t not in closes.columns or closes[t].dropna().empty]
    return closes, missing
