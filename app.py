"""
Model Portfolio Tracker -- Streamlit app.

View: anyone with the link. Edit: after entering the edit PIN (sidebar), if one
is configured in secrets. Data lives in data/portfolio.json in this app's own
GitHub repo (see storage.py); prices come from yfinance (see prices.py).
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core
import notify
import prices
from storage import ConflictError, GitHubStore, LocalStore

st.set_page_config(page_title="Model Portfolio", page_icon="📈", layout="wide")

# Reference categorical palette (fixed order, validated) + diverging poles.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
POS, NEG = "#2a78d6", "#e34948"
GRID = "rgba(128,128,128,0.18)"

DEFAULT_BENCH = {
    "INR": {"Nifty 50": "^NSEI", "Nifty 500": "^CRSLDX", "Nifty Smallcap 250": "NIFTYSMLCAP250.NS"},
    "USD": {"S&P 500": "^GSPC", "Nasdaq 100": "^NDX", "Russell 2000": "^RUT"},
}


# ── storage ───────────────────────────────────────────────────────────────
def _secret(section, key, default=None):
    try:
        return st.secrets[section][key]
    except Exception:
        return default


@st.cache_resource
def _make_store(token, repo, path, branch):
    # Keyed on the secret values, so adding or changing secrets takes effect
    # on the next page load -- no app reboot needed.
    if token and repo:
        return GitHubStore(token, repo, path, branch)
    return LocalStore(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "portfolio.json"))


def get_store():
    return _make_store(_secret("github", "token"), _secret("github", "repo"),
                       _secret("github", "path", "data/portfolio.json"), _secret("github", "branch", "main"))


@st.cache_data(ttl=60, show_spinner=False)
def load_data():
    return get_store().load()


def save_data(data: dict, version, message: str) -> bool:
    try:
        get_store().save(data, version, message)
    except ConflictError as e:
        st.error(f"{e} Reload the page and redo this change.")
        load_data.clear()
        return False
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False
    load_data.clear()
    st.session_state["flash"] = f"Saved: {message}"
    return True


PRICE_CACHE_VERSION = 2   # bump whenever fetch_closes' return shape changes


@st.cache_data(ttl=4 * 3600, show_spinner="Fetching prices…")
def _cached_closes(tickers: tuple, start: str, seed: tuple, version: int):
    return prices.fetch_closes(list(tickers), start, dict(seed))


def get_closes(tickers: tuple, start: str, seed: tuple):
    """Always returns (closes, splits, missing), even if an older cached
    result without splits is still around after an update."""
    res = _cached_closes(tickers, start, seed, PRICE_CACHE_VERSION)
    if len(res) == 2:
        return res[0], pd.DataFrame(), res[1]
    return res


@st.cache_data(ttl=3600, show_spinner=False)
def last_price(sym: str):
    """Latest close for one symbol (reference price when planning an entry)."""
    try:
        cl, _, _ = prices.fetch_closes([sym], (pd.Timestamp.today() - pd.Timedelta(days=15)).strftime("%Y-%m-%d"))
        v = cl[sym].dropna()
        return round(float(v.iloc[-1]), 2) if len(v) else None
    except Exception:
        return None


# ── formatting ────────────────────────────────────────────────────────────
def _indian(n: float) -> str:
    s = f"{abs(n):,.0f}".replace(",", "")
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail]) if parts else tail


def money(v, cur="INR") -> str:
    if v is None or pd.isna(v):
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}₹{_indian(v)}" if cur == "INR" else f"{sign}${abs(v):,.0f}"


def pct(v, signed=True) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


KPI_CSS = """<style>
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:4px 0 14px}
.kpi{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:10px 14px;min-width:0}
.kpi-l{font-size:.82rem;opacity:.68;margin-bottom:2px}
.kpi-v{font-size:clamp(1.15rem,1.9vw,1.65rem);font-weight:600;line-height:1.25;overflow-wrap:anywhere}
.kpi-s{font-size:.8rem;margin-top:3px;opacity:.8;overflow-wrap:anywhere}
.kpi-s.pos{color:#2a78d6;opacity:1}.kpi-s.neg{color:#e34948;opacity:1}
</style>"""


def kpis(cards):
    """cards: list of (label, value, sub, tone, tooltip). A CSS grid that wraps to
    as many rows as the screen needs, so no value is ever cut off (st.metric
    truncates long values with '…' on narrower screens)."""
    import html
    cells = []
    for label, value, sub, tone, tip in cards:
        t = f' title="{html.escape(tip)}"' if tip else ""
        sub_html = f'<div class="kpi-s {tone or ""}">{html.escape(sub)}</div>' if sub else ""
        cells.append(f'<div class="kpi"{t}><div class="kpi-l">{html.escape(label)}{" ⓘ" if tip else ""}</div>'
                     f'<div class="kpi-v">{html.escape(str(value))}</div>{sub_html}</div>')
    st.markdown(KPI_CSS + '<div class="kpi-grid">' + "".join(cells) + "</div>", unsafe_allow_html=True)


def tone(v):
    return "" if v is None or pd.isna(v) or v == 0 else ("pos" if v > 0 else "neg")


def style_line_chart(fig, y_title):
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10), height=380, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(title=y_title, gridcolor=GRID, zeroline=True, zerolinecolor=GRID, ticksuffix="%")
    return fig


MARKET_HOURS = {"INR": ("Asia/Kolkata", (9, 15), (15, 30)), "USD": ("America/New_York", (9, 30), (16, 0))}


def price_label(px_date: pd.Timestamp, currency: str) -> str:
    """'close' only once the exchange has actually closed for that day; during
    trading hours Yahoo's latest bar is the price so far, not a close."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    tz, open_t, close_t = MARKET_HOURS.get(currency, MARKET_HOURS["INR"])
    now = datetime.now(ZoneInfo(tz))
    if px_date.date() == now.date() and now.weekday() < 5 and time(*open_t) <= now.time() < time(*close_t):
        return f"Prices intraday as of {now.strftime('%H:%M')} today (market open; final at close)"
    return f"Prices as of {px_date.strftime('%d %b %Y')} close"


def pnl_color(v):
    if v is None or pd.isna(v):
        return ""
    return f"color: {POS}" if v > 0 else (f"color: {NEG}" if v < 0 else "")


# ── load ──────────────────────────────────────────────────────────────────
try:
    data, version = load_data()
except FileNotFoundError:
    data, version = core.empty_data(), None
except Exception as e:
    st.error(f"Could not load portfolio data from {get_store().label}: {e}")
    st.stop()

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

edit_pin = _secret("app", "edit_pin")
editing = (not edit_pin) or st.session_state.get("unlocked", False)

with st.sidebar:
    st.header("Model Portfolio")
    pids = list(data["portfolios"].keys())
    if pids:
        pid = st.selectbox("Portfolio", pids, format_func=lambda p: data["portfolios"][p].get("name", p))
    else:
        pid = None
    if st.button("↻ Refresh prices", width="stretch", help="Prices are cached for 4 hours."):
        _cached_closes.clear()
        st.rerun()
    st.divider()
    if edit_pin and not editing:
        pin = st.text_input("Edit PIN", type="password", help="Only needed to make changes.")
        if pin:
            if pin == str(edit_pin):
                st.session_state["unlocked"] = True
                st.rerun()
            else:
                st.error("Wrong PIN")
    elif edit_pin:
        st.success("Edit mode on")
        if st.button("Lock editing", width="stretch"):
            st.session_state["unlocked"] = False
            st.rerun()
    st.caption(f"Data: {get_store().label}")


# ── first-run: no portfolio yet ───────────────────────────────────────────
def new_portfolio_form(key: str):
    with st.form(key):
        c1, c2, c3, c4 = st.columns(4)
        name = c1.text_input("Portfolio name", placeholder="e.g. US Momentum")
        cur = c2.selectbox("Currency", ["INR", "USD"])
        cap = c3.number_input("Starting capital", min_value=1.0, value=2000000.0, step=100000.0)
        slots = c4.number_input("Equal-weight slots", min_value=1, value=20, step=1)
        if st.form_submit_button("Create portfolio"):
            if not name.strip():
                st.error("Give it a name.")
                return
            new_id = "".join(ch for ch in name if ch.isalnum()) or core.new_lot_id()
            if new_id in data["portfolios"]:
                st.error("A portfolio with that name already exists.")
                return
            data["portfolios"][new_id] = {"name": name.strip(), "currency": cur, "capital": float(cap),
                                          "slots": int(slots), "benchmarks": DEFAULT_BENCH[cur]}
            if save_data(data, version, f"Create portfolio {name.strip()}"):
                st.rerun()


if not pid:
    st.title("Model Portfolio")
    st.info("No portfolio yet." + ("" if editing else " Unlock editing in the sidebar to create one."))
    if editing:
        new_portfolio_form("first_portfolio")
    st.stop()

cfg = data["portfolios"][pid]
cur = cfg.get("currency", "INR")
capital = float(cfg.get("capital", 0) or 0)
slots = int(cfg.get("slots", 20) or 20)
benchmarks = cfg.get("benchmarks") or DEFAULT_BENCH.get(cur, {})
lots = core.lots_frame(data, pid)

st.title(cfg.get("name", pid))

# ── prices & analytics ────────────────────────────────────────────────────
have_lots = not lots.empty
if have_lots:
    start = (lots["entry_date"].min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    seed = tuple(sorted({**{s: float(g["entry_price"].iloc[0]) for s, g in lots.groupby("symbol")},
                          **{t: 1000.0 for t in benchmarks.values()}}.items()))
    tickers = tuple(sorted(set(lots["symbol"]) | set(benchmarks.values())))
    try:
        closes, splits, missing = get_closes(tickers, start, seed)
    except Exception as e:
        closes, splits, missing = pd.DataFrame(), pd.DataFrame(), list(tickers)
        st.warning(f"Price download failed ({e}). Showing entry/manual prices until the next refresh.")
    stock_cols = [c for c in closes.columns if c in set(lots["symbol"])]
    bench_cols = {lbl: t for lbl, t in benchmarks.items() if t in closes.columns}
    end = pd.Timestamp.today().normalize()
    if stock_cols:
        lots = core.apply_split_basis(lots, closes[stock_cols], splits)
    px = core.price_matrix(lots, closes[stock_cols] if stock_cols else pd.DataFrame(), end)
    last = core.latest_prices(lots, px)
    eq = core.equity_curve(lots, px, capital)
    bench = core.benchmark_curves(closes[list(bench_cols.values())].rename(
        columns={v: k for k, v in bench_cols.items()}) if bench_cols else pd.DataFrame(), eq.index)
    closed = core.closed_trades(lots, last, priced=set(stock_cols) - set(missing))
    hold = core.holdings_table(lots, last, float(eq["equity"].iloc[-1]))
    s = core.stats(eq, closed, bench, capital)
    open_missing = sorted(set(missing) & set(lots.loc[lots["is_open"], "symbol"]))
    if open_missing:
        st.warning("No market price found for: " + ", ".join(open_missing)
                   + ". Using manual price if set (Manage → Edit lots), otherwise entry price.")
    px_date = closes.index.max() if not closes.empty else None
    if px_date is not None:
        st.caption(f"{price_label(pd.Timestamp(px_date), cur)} · "
                   f"tracking since {s['since'].strftime('%d %b %Y')}")

tabs = st.tabs(["Overview", "Holdings", "Weekly changes", "Closed trades", "Analytics"]
               + (["Manage"] if editing else []))

# ── Overview ──────────────────────────────────────────────────────────────
with tabs[0]:
    if not have_lots:
        st.info("No positions yet.")
    else:
        _pl = (cfg.get("plan") or {}).get("items") or []
        if _pl:
            _x = ", ".join(i["symbol"].split(".")[0] for i in _pl if i["action"] == "exit") or "none"
            _e = ", ".join(i["symbol"].split(".")[0] for i in _pl if i["action"] == "entry") or "none"
            st.info(f"📋 **Plan for next session** — exit: {_x} · enter: {_e}")
        kpis([
            ("Portfolio value", money(s["equity"], cur), f"{pct(s['total_return_pct'])} since start",
             tone(s["total_return_pct"]), None),
            ("Total P&L", money(s["total_pnl"], cur),
             f"Realised {money(s['realised'], cur)} · Open {money(s['unrealised'], cur)}", "", None),
            ("CAGR", pct(s["cagr_pct"]) if s["cagr_pct"] is not None else "—", "annualised", "",
             "Compound annual growth since the first entry. Shown once the record is 3+ months long."),
            ("Max drawdown", pct(s["max_dd_pct"]), f"now {pct(s['current_dd_pct'])}", "", None),
            ("Win rate", pct(s.get("win_rate_pct"), signed=False) if s.get("n_closed") else "—",
             f"{s.get('n_closed', 0)} closed trades", "", None),
        ])

        if s["bench"]:
            st.caption("Same period · " + " · ".join(
                f"{k} {pct(v)} (portfolio {'+' if s['total_return_pct'] - v >= 0 else ''}"
                f"{s['total_return_pct'] - v:.2f} pts)" for k, v in s["bench"].items()))

        st.subheader("Return since start")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq["return_pct"], name=cfg.get("name", pid),
                                 line=dict(color=SERIES[0], width=2.5),
                                 hovertemplate="%{y:+.2f}%"))
        for i, col in enumerate(bench.columns):
            fig.add_trace(go.Scatter(x=bench.index, y=bench[col], name=col,
                                     line=dict(color=SERIES[i + 1], width=1.5),
                                     hovertemplate="%{y:+.2f}%"))
        st.plotly_chart(style_line_chart(fig, "Return"), width="stretch")

        st.subheader("Drawdown")
        dd = go.Figure(go.Scatter(x=eq.index, y=eq["drawdown_pct"], fill="tozeroy", name="Drawdown",
                                  line=dict(color=NEG, width=1.5), fillcolor="rgba(227,73,72,0.15)",
                                  hovertemplate="%{y:.2f}%"))
        st.plotly_chart(style_line_chart(dd, "From peak").update_layout(height=220, showlegend=False),
                        width="stretch")

# ── Holdings ──────────────────────────────────────────────────────────────
with tabs[1]:
    if not have_lots or hold.empty:
        st.info("No open positions.")
    else:
        upnl = hold["pnl"].sum()
        kpis([
            ("Open positions", f"{len(hold)} / {slots}", "slots used", "", None),
            ("Invested", money(hold["cost"].sum(), cur), "at cost", "", None),
            ("Market value", money(hold["value"].sum(), cur), "", "", None),
            ("Unrealised P&L", money(upnl, cur), pct(upnl / hold["cost"].sum() * 100), tone(upnl), None),
        ])
        if hold["split_adj"].any():
            st.caption("Qty and entry price are on today's share basis for positions that had a split or bonus "
                       f"since entry ({', '.join(sorted(hold.loc[hold['split_adj'], 'symbol']))}).")
        _px = {i.get("lot_id") for i in ((cfg.get("plan") or {}).get("items") or []) if i["action"] == "exit"}
        hold = hold.assign(plan=["Exit planned" if i in _px else "" for i in hold["id"]])
        view = hold[["symbol", "plan", "name", "entry_date", "entry_price", "qty", "ltp", "value", "pnl",
                     "pnl_pct", "weight_pct", "days", "comment"]]
        st.dataframe(
            view.style.map(pnl_color, subset=["pnl", "pnl_pct"]),
            hide_index=True, width="stretch", height=min(38 * (len(view) + 1) + 4, 780),
            column_config={
                "symbol": st.column_config.TextColumn("Stock", pinned=True), "plan": "Plan", "name": "Name",
                "entry_date": st.column_config.DateColumn("Entry", format="DD MMM YY"),
                "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
                "qty": st.column_config.NumberColumn("Qty", format="%g"),
                "ltp": st.column_config.NumberColumn("Last", format="%.2f"),
                "value": st.column_config.NumberColumn("Value", format="localized"),
                "pnl": st.column_config.NumberColumn("P&L", format="localized"),
                "pnl_pct": st.column_config.NumberColumn("P&L %", format="%+.2f%%"),
                "weight_pct": st.column_config.ProgressColumn("Weight", format="%.1f%%", min_value=0,
                                                              max_value=max(10.0, float(view["weight_pct"].max()))),
                "days": st.column_config.NumberColumn("Days", format="%d"),
                "comment": "Comment",
            })

# ── Weekly changes ────────────────────────────────────────────────────────
with tabs[2]:
    ch = core.weekly_changes(lots) if have_lots else pd.DataFrame()
    if ch.empty:
        st.info("No changes yet.")
    else:
        for i, (wk, g) in enumerate(ch.groupby("week", sort=False)):
            n_in, n_out = (g["action"] == "Entry").sum(), (g["action"] == "Exit").sum()
            label = f"Week of {wk.strftime('%d %b %Y')} — {n_in} entr{'y' if n_in == 1 else 'ies'}, {n_out} exit{'' if n_out == 1 else 's'}"
            with st.expander(label, expanded=i < 2):
                for act in ["Entry", "Exit"]:
                    col = st
                    sub = g[g["action"] == act][["date", "symbol", "name", "price", "qty", "pnl_pct", "comment"]]
                    col.markdown(f"**{'Entries' if act == 'Entry' else 'Exits'}**")
                    if sub.empty:
                        col.caption("No entries" if act == "Entry" else "No exits")
                        continue
                    if act == "Entry":
                        sub = sub.drop(columns=["pnl_pct"])
                    col.dataframe(
                        sub.style.map(pnl_color, subset=["pnl_pct"]) if act == "Exit" else sub,
                        hide_index=True, width="stretch",
                        column_config={"date": st.column_config.DateColumn("Date", format="DD MMM"),
                                       "symbol": "Stock", "name": "Name",
                                       "price": st.column_config.NumberColumn("Price", format="%.2f"),
                                       "qty": st.column_config.NumberColumn("Qty", format="%g"),
                                       "pnl_pct": st.column_config.NumberColumn("Trade P&L", format="%+.2f%%"),
                                       "comment": "Comment"})

# ── Closed trades ─────────────────────────────────────────────────────────
with tabs[3]:
    if not have_lots or closed.empty:
        st.info("No closed trades yet.")
    else:
        days = lambda v: f"{v:.0f} days" if v is not None and not pd.isna(v) else "—"
        post = closed["since_exit_pct"].dropna()
        kpis([
            ("Closed trades", s["n_closed"], f"Realised {money(closed['pnl'].sum(), cur)}",
             tone(closed["pnl"].sum()), None),
            ("Win rate", pct(s["win_rate_pct"], signed=False), None, "", None),
            ("Avg win", pct(s["avg_win_pct"]), f"held {days(s.get('avg_days_win'))}", "pos", None),
            ("Avg loss", pct(s["avg_loss_pct"]), f"held {days(s.get('avg_days_loss'))}", "neg", None),
            ("Profit factor", f"{s['profit_factor']:.2f}" if s.get("profit_factor") else "—", None, "",
             "Total gains ÷ total losses on closed trades. Above 1 = the system makes money."),
            ("After exit (median)", pct(post.median()) if len(post) else "—",
             f"{(post > 0).sum()} of {len(post)} rose after exit" if len(post) else None, "",
             "How exited stocks moved from the exit price to today's price. Positive = the stock kept "
             "rising after we sold; negative = the exit avoided a further fall."),
        ])
        st.caption(f"Best {s['best'][0]} {pct(s['best'][1])} · Worst {s['worst'][0]} {pct(s['worst'][1])}")
        view = closed[["symbol", "name", "entry_date", "entry_price", "exit_date", "exit_price", "qty",
                       "pnl", "pnl_pct", "since_exit_pct", "ltp", "days", "comment"]]
        st.dataframe(
            view.style.map(pnl_color, subset=["pnl", "pnl_pct", "since_exit_pct"]), hide_index=True, width="stretch",
            column_config={
                "symbol": st.column_config.TextColumn("Stock", pinned=True), "name": "Name",
                "entry_date": st.column_config.DateColumn("Entry", format="DD MMM YY"),
                "entry_price": st.column_config.NumberColumn("Entry price", format="%.2f"),
                "exit_date": st.column_config.DateColumn("Exit", format="DD MMM YY"),
                "exit_price": st.column_config.NumberColumn("Exit price", format="%.2f"),
                "qty": st.column_config.NumberColumn("Qty", format="%g"),
                "pnl": st.column_config.NumberColumn("P&L", format="localized"),
                "pnl_pct": st.column_config.NumberColumn("P&L %", format="%+.2f%%"),
                "days": st.column_config.NumberColumn("Days", format="%d"),
                "ltp": st.column_config.NumberColumn("Last", format="%.2f",
                                                     help="Latest price on today's share basis."),
                "since_exit_pct": st.column_config.NumberColumn(
                    "Since exit", format="%+.2f%%",
                    help="Move from the exit price to the latest price, adjusted for any later split or bonus. "
                         "Positive = kept rising after the exit."),
                "comment": "Comment"})

# ── Analytics ─────────────────────────────────────────────────────────────
with tabs[4]:
    if not have_lots:
        st.info("Nothing to analyse yet.")
    else:
        st.subheader("Monthly returns")
        mr = core.monthly_returns(eq, capital)

        def cell(v):
            if pd.isna(v):
                return ""
            a = min(abs(v) / 10, 1) * 0.55 + 0.08
            rgb = "42,120,214" if v > 0 else "227,73,72"
            return f"background-color: rgba({rgb},{a:.2f})"

        shown = mr.apply(lambda col: col.map(lambda v: "" if pd.isna(v) else f"{v:+.1f}%"))
        shown.index = shown.index.astype(str)
        colors = mr.apply(lambda col: col.map(cell))
        colors.index = shown.index
        st.dataframe(shown.style.apply(lambda _: colors, axis=None), width="stretch")
        yrs = eq.index.year
        st.caption(f"Year = the months compounded (portfolio value at year end ÷ previous year end), not added up. "
                   f"{yrs.min()} covers {eq.index[0]:%d %b}–31 Dec only, and {yrs.max()} is year to date. "
                   f"The annualised figure (CAGR since start) is on the Overview.")

        st.subheader("P&L contribution by stock")
        con = core.contribution(lots, last)
        n = st.slider("Show top and bottom", 5, 30, 12, key="contrib_n")
        show = pd.concat([con.head(n), con.tail(n)]).drop_duplicates()
        show = show.iloc[::-1]
        bar = go.Figure(go.Bar(
            x=show["pnl"], y=[f"{i}{' •' if o else ''}" for i, o in zip(show.index, show["open"])],
            orientation="h", marker_color=[POS if v > 0 else NEG for v in show["pnl"]],
            hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
        bar.update_layout(height=max(320, 22 * len(show)), margin=dict(l=10, r=10, t=10, b=10),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        bar.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, title="P&L")
        st.plotly_chart(bar, width="stretch")
        st.caption("• = still held (includes unrealised P&L). Re-entries are summed per stock.")

        if not closed.empty:
            st.subheader("Closed-trade outcomes")
            h = go.Figure(go.Histogram(x=closed["pnl_pct"], nbinsx=25, marker_color=SERIES[0],
                                       marker_line=dict(width=1, color="white"),
                                       hovertemplate="%{x}: %{y} trades<extra></extra>"))
            h.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10),
                            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            h.update_xaxes(title="Trade return", ticksuffix="%", gridcolor=GRID)
            h.update_yaxes(title="Trades", gridcolor=GRID)
            st.plotly_chart(h, width="stretch")

        st.subheader("Exposure")
        ex = go.Figure(go.Scatter(x=eq.index, y=eq["exposure_pct"], name="Invested",
                                  line=dict(color=SERIES[0], width=1.5), hovertemplate="%{y:.1f}%"))
        st.plotly_chart(style_line_chart(ex, "Of portfolio value").update_layout(height=220, showlegend=False),
                        width="stretch")
        st.caption("Share of portfolio value in open positions (cost basis). The rest is notional cash.")

# ── Manage (edit mode only) ───────────────────────────────────────────────
if editing:
    with tabs[5]:
        equity_now = float(eq["equity"].iloc[-1]) if have_lots else capital
        tg_token = _secret("telegram", "bot_token")
        chat_id = cfg.get("telegram_chat_id")
        tg_ready = bool(tg_token and chat_id)
        name = cfg.get("name", pid)

        def tg_send(text: str) -> bool:
            try:
                notify.send_message(tg_token, chat_id, text)
                return True
            except Exception as e:
                st.error(f"Telegram didn't accept the message: {e}")
                return False

        def preview(msg: str):
            st.markdown("**Preview**")
            with st.container(border=True):
                st.markdown(notify.preview_markdown(msg))

        def now_stamp() -> str:
            return pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y %H:%M")

        def tg_hint():
            st.info("Telegram isn't set up yet: " + ("add the bot token to Secrets, then " if not tg_token else "")
                    + "connect the group under Manage → Settings.")

        plan = cfg.get("plan") or {"items": [], "published": None, "published_at": None}
        items = plan["items"]

        def save_plan(msg: str):
            cfg["plan"] = plan if (plan["items"] or plan.get("published")) else None
            if save_data(data, version, f"{name}: {msg}"):
                st.rerun()

        mp, mx, m1, m2, m3, m6, m4, m5 = st.tabs(["Plan", "Execute", "Add entry", "Close position", "Edit lots",
                                                  "Weekly summary", "Settings", "New portfolio"])

        # ── Plan (evening) ──
        with mp:
            st.caption("Evening: list tomorrow's exits and entries and publish them to the group. Changing the "
                       "plan after publishing? Publish again and only the changes are sent.")
            open_lots = lots[lots["is_open"]].sort_values("symbol") if have_lots else pd.DataFrame()
            planned_exit_ids = {i.get("lot_id") for i in items if i["action"] == "exit"}
            exit_opts = {r.id: f"{r.symbol} — {r.q:g} sh" for r in open_lots.itertuples()
                         if r.id not in planned_exit_ids} if not open_lots.empty else {}
            c = st.columns([3, 3, 1.2], vertical_alignment="bottom")
            lid = c[0].selectbox("Exit", list(exit_opts), format_func=exit_opts.get, index=None,
                                 placeholder="Pick a holding")
            why_x = c[1].text_input("Reason (optional)", key="plan_exit_note")
            if c[2].button("Add exit", disabled=lid is None, width="stretch"):
                lot = open_lots[open_lots["id"] == lid].iloc[0]
                items.append({"id": core.new_lot_id(), "action": "exit", "symbol": lot["symbol"],
                              "name": lot["name"], "lot_id": lid, "note": why_x.strip() or None, "ref_price": None})
                save_plan(f"plan exit {lot['symbol']}")
            c = st.columns([3, 3, 1.2], vertical_alignment="bottom")
            sym_e = c[0].text_input("Enter", key="plan_entry_sym", placeholder="HFCL or 543210.BO").strip().upper()
            why_e = c[1].text_input("Reason (optional)", key="plan_entry_note")
            if c[2].button("Add entry", disabled=not sym_e, width="stretch"):
                if cur == "INR" and "." not in sym_e:
                    sym_e += ".NS"
                if any(i["action"] == "entry" and i["symbol"] == sym_e for i in items):
                    st.warning(f"{sym_e} is already in the plan.")
                else:
                    items.append({"id": core.new_lot_id(), "action": "entry", "symbol": sym_e,
                                  "name": sym_e.split(".")[0], "lot_id": None, "note": why_e.strip() or None,
                                  "ref_price": last_price(sym_e)})
                    save_plan(f"plan entry {sym_e}")

            if not items:
                st.info("No plan yet.")
            else:
                tbl = pd.DataFrame([{"Action": "Exit" if i["action"] == "exit" else "Enter",
                                     "Stock": i["symbol"], "Ref price": i.get("ref_price") or last.get(i["symbol"]),
                                     "Reason": i.get("note") or ""} for i in items])
                st.dataframe(tbl, hide_index=True, width="stretch",
                             column_config={"Ref price": st.column_config.NumberColumn(format="%.2f")})
                labels = {i["id"]: f"{'Exit' if i['action'] == 'exit' else 'Enter'} {i['symbol']}" for i in items}
                c = st.columns([4, 1.2, 1.2], vertical_alignment="bottom")
                drop = c[0].multiselect("Remove from plan", list(labels), format_func=labels.get)
                if c[1].button("Remove", disabled=not drop, width="stretch"):
                    plan["items"] = [i for i in items if i["id"] not in drop]
                    save_plan("plan remove " + ", ".join(labels[d] for d in drop))
                if c[2].button("Clear plan", width="stretch"):
                    plan.update(items=[], published=None, published_at=None)
                    save_plan("plan cleared")

                st.divider()
                if not tg_ready:
                    tg_hint()
                else:
                    note = st.text_input("Note for the group (optional)", key="plan_note")
                    if plan.get("published") is None:
                        msg = notify.plan_message(name, items, note.strip() or None, cfg.get("app_url"))
                        preview(msg)
                        if st.button("Publish plan", type="primary") and tg_send(msg):
                            plan.update(published=[dict(i) for i in items], published_at=now_stamp())
                            save_plan("plan published")
                    else:
                        added, removed = notify.plan_diff(plan["published"], items)
                        if not added and not removed:
                            st.success(f"Published {plan['published_at']} — no changes since.")
                        else:
                            msg = notify.plan_update_message(name, added, removed, items, note.strip() or None)
                            preview(msg)
                            if st.button("Publish update", type="primary") and tg_send(msg):
                                plan.update(published=[dict(i) for i in items], published_at=now_stamp())
                                save_plan("plan update published")

        # ── Execute (morning) ──
        with mx:
            ex_last = cfg.get("last_execution")
            if not items:
                st.info("No plan to execute. Add it under Plan first.")
            else:
                st.caption("Morning: enter the actual fill prices and confirm. Couldn't buy a stock (e.g. upper "
                           "circuit)? Type the replacement symbol in its row, its fill price and a reason. Untick anything not "
                           "done — it stays in the plan. Leave Qty blank for equal weight.")
                d_exec = st.date_input("Execution date", value=date.today(), key="exec_date")
                rows = pd.DataFrame([{
                    "id": i["id"], "Action": "Exit" if i["action"] == "exit" else "Enter",
                    "Stock": i["symbol"], "Done": True,
                    "Fill price": round(float(last.get(i["symbol"]) or i.get("ref_price") or 0) or 0, 2) or None,
                    "Qty": None, "Reason": ""} for i in items])
                ed = st.data_editor(
                    rows, hide_index=True, width="stretch", key="exec_editor",
                    disabled=["Action"],
                    column_config={"id": None,
                                   "Stock": st.column_config.TextColumn(help="Change an entry's symbol to buy a "
                                                                             "replacement instead."),
                                   "Done": st.column_config.CheckboxColumn(help="Untick if it couldn't be done."),
                                   "Fill price": st.column_config.NumberColumn(format="%.2f", min_value=0.0),
                                   "Qty": st.column_config.NumberColumn(help="Blank = equal weight (entries) or "
                                                                             "the full position (exits)."),
                                   "Reason": st.column_config.TextColumn(help="Why it changed or wasn't done.")})
                if st.button("Confirm execution", type="primary"):
                    by_id = {i["id"]: i for i in items}
                    errors = []
                    recs = ed.to_dict("records")
                    num = lambda v: float(v) if v is not None and not pd.isna(v) else 0.0
                    txt = lambda v: str(v).strip() if v is not None and not pd.isna(v) else ""
                    for r in recs:
                        it = by_id[r["id"]]
                        if r["Done"] and num(r["Fill price"]) <= 0:
                            errors.append(f"{r['Stock']}: fill price missing")
                        if it["action"] == "exit" and txt(r["Stock"]).upper() != it["symbol"]:
                            errors.append(f"{it['symbol']}: an exit's symbol can't be changed")
                    if errors:
                        st.error("; ".join(errors))
                    else:
                        lots_by_id = {l["id"]: l for l in data["lots"]}
                        basis = lots.set_index("id") if have_lots else pd.DataFrame()
                        rec = {"date": d_exec.isoformat(), "exits": [], "entries": [], "replaced": [],
                               "skipped": [], "sent_at": None}
                        keep = []
                        eq_now = equity_now
                        for r in recs:
                            it, fill = by_id[r["id"]], num(r["Fill price"])
                            reason = txt(r["Reason"]) or None
                            if not r["Done"]:
                                keep.append(it)
                                rec["skipped"].append({"action": it["action"], "symbol": it["symbol"], "reason": reason})
                                continue
                            if it["action"] == "exit":
                                l = lots_by_id.get(it["lot_id"])
                                if not l or l.get("exit_date"):
                                    continue
                                q_out = num(r["Qty"])
                                sold = float(l["qty"])
                                if 0 < q_out < float(l["qty"]) - 1e-9:   # partial exit: split the lot
                                    sold = q_out
                                    data["lots"].append(dict(l, id=core.new_lot_id(), qty=q_out,
                                                             exit_date=d_exec.isoformat(), exit_price=fill))
                                    l["qty"] = float(l["qty"]) - q_out
                                    keep.append(dict(it))            # rest stays planned
                                else:
                                    l.update(exit_date=d_exec.isoformat(), exit_price=fill)
                                e_px = float(basis.loc[it["lot_id"], "e_px"]) if it["lot_id"] in basis.index else l["entry_price"]
                                rec["exits"].append({"symbol": it["symbol"], "price": fill,
                                                     "qty": sold,
                                                     "pnl_pct": (fill / e_px - 1) * 100 if e_px else None})
                            else:
                                sym_x = txt(r["Stock"]).upper()
                                if cur == "INR" and "." not in sym_x:
                                    sym_x += ".NS"
                                qty = num(r["Qty"]) or core.equal_weight_qty(eq_now, slots, fill)
                                data["lots"].append({"id": core.new_lot_id(), "portfolio": pid, "symbol": sym_x,
                                                     "name": sym_x.split(".")[0], "entry_date": d_exec.isoformat(),
                                                     "entry_price": fill, "qty": qty, "exit_date": None,
                                                     "exit_price": None, "commission": 0.0, "manual_price": None,
                                                     "comment": reason})
                                rec["entries"].append({"symbol": sym_x, "price": fill, "qty": qty})
                                if sym_x != it["symbol"]:
                                    rec["replaced"].append({"from": it["symbol"], "to": sym_x, "reason": reason})
                        plan["items"] = keep
                        if not keep:
                            plan.update(published=None, published_at=None)
                        cfg["plan"] = plan if keep else None
                        cfg["last_execution"] = rec
                        if save_data(data, version, f"{name}: executed {d_exec:%d %b} "
                                     f"({len(rec['exits'])} exits, {len(rec['entries'])} entries)"):
                            st.rerun()

            if ex_last:
                st.divider()
                st.markdown(f"**Last execution — {pd.Timestamp(ex_last['date']):%d %b %Y}**")
                if not tg_ready:
                    tg_hint()
                else:
                    held = [(r.symbol, r.pnl_pct) for r in hold.sort_values("pnl_pct", ascending=False).itertuples()] \
                        if have_lots and not hold.empty else []
                    msg = notify.executed_message(name, ex_last, held, s if have_lots else {}, cfg.get("app_url"))
                    preview(msg)
                    again = True
                    if ex_last.get("sent_at"):
                        st.caption(f"Sent {ex_last['sent_at']}.")
                        again = st.checkbox("Send it again", key="exec_again")
                    if st.button("Send executed message", type="primary", disabled=not again) and tg_send(msg):
                        ex_last["sent_at"] = now_stamp()
                        if save_data(data, version, f"{name}: executed message sent"):
                            st.rerun()

        # ── Weekly summary ──
        with m6:
            if not tg_ready:
                tg_hint()
            elif have_lots and not ch.empty:
                st.caption("Optional recap of a whole week's changes.")
                weeks = list(dict.fromkeys(ch["week"]))
                wk = st.selectbox("Week", weeks, format_func=lambda w: f"Week of {w:%d %b %Y}")
                note = st.text_input("Optional note", key="week_note")
                msg = notify.week_message(name, wk, ch[ch["week"] == wk], s, cfg.get("app_url"),
                                          note.strip() or None)
                preview(msg)
                sent = (cfg.get("published_weeks") or {}).get(wk.strftime("%Y-%m-%d"))
                again = True
                if sent:
                    st.warning(f"This week was already posted on {sent}.")
                    again = st.checkbox("Post it again anyway")
                if st.button("Send weekly summary", type="primary", disabled=not again) and tg_send(msg):
                    cfg.setdefault("published_weeks", {})[wk.strftime("%Y-%m-%d")] = now_stamp()
                    if save_data(data, version, f"{name}: published week of {wk:%d %b}"):
                        st.rerun()

        with m1:
            c = st.columns([2, 3, 2])
            sym = c[0].text_input("Symbol", placeholder="HFCL or HFCL.NS or 543210.BO").strip().upper()
            nm = c[1].text_input("Name (optional)")
            d_in = c[2].date_input("Entry date", value=date.today())
            c = st.columns([2, 2, 3])
            price_in = c[0].number_input("Entry price", min_value=0.0, value=0.0, step=0.05, format="%.2f")
            ew = core.equal_weight_qty(equity_now, slots, price_in)
            override = c[1].checkbox("Override quantity", help=f"Default is equal weight: portfolio value ÷ {slots} slots.")
            qty_in = c[1].number_input("Quantity", min_value=0.0, value=float(ew), step=1.0) if override else ew
            if price_in > 0:
                c[2].caption(f"Equal weight: {money(equity_now / slots, cur)} → **{ew:g} shares** "
                             f"({money(ew * price_in, cur)})")
            note = st.text_input("Comment (optional)", key="entry_note")
            if st.button("Add entry", type="primary", disabled=not (sym and price_in > 0 and qty_in > 0)):
                if cur == "INR" and "." not in sym:
                    sym += ".NS"
                data["lots"].append({"id": core.new_lot_id(), "portfolio": pid, "symbol": sym,
                                     "name": nm or sym.split(".")[0], "entry_date": d_in.isoformat(),
                                     "entry_price": float(price_in), "qty": float(qty_in),
                                     "exit_date": None, "exit_price": None, "commission": 0.0,
                                     "manual_price": None, "comment": note or None})
                if save_data(data, version, f"{cfg.get('name', pid)}: entry {sym} {qty_in:g} @ {price_in:.2f}"):
                    st.rerun()

        with m2:
            op = lots[lots["is_open"]] if have_lots else pd.DataFrame()
            if op.empty:
                st.info("No open positions.")
            else:
                opts = {r.id: f"{r.symbol} — {r.qty:g} @ {r.entry_price:.2f} (since {r.entry_date:%d %b %y})"
                        for r in op.sort_values("symbol").itertuples()}
                lid = st.selectbox("Position", list(opts), format_func=opts.get)
                lot = op[op["id"] == lid].iloc[0]
                c = st.columns(3)
                d_out = c[0].date_input("Exit date", value=date.today(), key="exit_date")
                p_out = c[1].number_input("Exit price", min_value=0.0,
                                          value=float(round(last.get(lot["symbol"], lot["entry_price"]), 2)),
                                          step=0.05, format="%.2f")
                q_out = c[2].number_input("Quantity to sell", min_value=0.0, max_value=float(lot["qty"]),
                                          value=float(lot["qty"]), step=1.0,
                                          help="Less than the full quantity = partial exit; the rest stays open.")
                note_out = st.text_input("Comment (optional)", key="exit_note")
                if p_out > 0:
                    st.caption(f"Trade result: {pct((p_out / lot['entry_price'] - 1) * 100)} · "
                               f"{money((p_out - lot['entry_price']) * q_out, cur)}")
                if st.button("Close position", type="primary", disabled=not (p_out > 0 and q_out > 0)):
                    for l in data["lots"]:
                        if l["id"] != lid:
                            continue
                        if q_out < l["qty"] - 1e-9:
                            part = dict(l, id=core.new_lot_id(), qty=float(q_out))
                            part.update(exit_date=d_out.isoformat(), exit_price=float(p_out))
                            if note_out:
                                part["comment"] = note_out
                            l["qty"] = float(l["qty"] - q_out)
                            data["lots"].append(part)
                        else:
                            l.update(exit_date=d_out.isoformat(), exit_price=float(p_out))
                            if note_out:
                                l["comment"] = note_out
                        break
                    if save_data(data, version, f"{cfg.get('name', pid)}: exit {lot['symbol']} {q_out:g} @ {p_out:.2f}"):
                        st.rerun()

        with m3:
            st.caption("Edit any cell, add or delete rows, then save. Leave Exit date empty for open positions. "
                       "Manual price overrides the market price for stocks Yahoo doesn't cover.")
            cols = ["symbol", "name", "entry_date", "entry_price", "qty", "exit_date", "exit_price",
                    "commission", "manual_price", "comment", "id"]
            base = lots[cols].sort_values("entry_date", ascending=False).reset_index(drop=True) if have_lots \
                else pd.DataFrame(columns=cols)
            edited = st.data_editor(
                base, num_rows="dynamic", hide_index=True, width="stretch", key="lot_editor",
                column_config={
                    "symbol": st.column_config.TextColumn("Symbol", required=True),
                    "name": "Name",
                    "entry_date": st.column_config.DateColumn("Entry date", required=True),
                    "entry_price": st.column_config.NumberColumn("Entry price", required=True, format="%.2f"),
                    "qty": st.column_config.NumberColumn("Qty", required=True),
                    "exit_date": st.column_config.DateColumn("Exit date"),
                    "exit_price": st.column_config.NumberColumn("Exit price", format="%.2f"),
                    "commission": st.column_config.NumberColumn("Commission", format="%.2f"),
                    "manual_price": st.column_config.NumberColumn("Manual price", format="%.2f"),
                    "comment": "Comment", "id": None})
            if st.button("Save lot changes", type="primary"):
                bad = edited[edited["exit_date"].notna() & edited["exit_price"].isna()]
                if len(bad):
                    st.error("Rows with an exit date also need an exit price: " + ", ".join(bad["symbol"].astype(str)))
                else:
                    others = [l for l in data["lots"] if l.get("portfolio") != pid]
                    data["lots"] = others + core.frame_to_lots(edited, pid)
                    if save_data(data, version, f"{cfg.get('name', pid)}: edit lots"):
                        st.rerun()

        with m4:
            with st.form("settings"):
                c = st.columns(4)
                nm = c[0].text_input("Portfolio name", value=cfg.get("name", pid))
                cur_new = c[1].selectbox("Currency", ["INR", "USD"], index=["INR", "USD"].index(cur))
                cap_new = c[2].number_input("Starting capital", min_value=1.0, value=capital, step=100000.0)
                slots_new = c[3].number_input("Equal-weight slots", min_value=1, value=slots, step=1)
                st.markdown("**Benchmarks** (yfinance tickers)")
                bdf = pd.DataFrame({"label": list(benchmarks.keys()), "ticker": list(benchmarks.values())})
                bed = st.data_editor(bdf, num_rows="dynamic", hide_index=True, key="bench_editor",
                                     column_config={"label": "Label", "ticker": "Ticker"})
                if st.form_submit_button("Save settings", type="primary"):
                    cfg.update(name=nm.strip() or pid, currency=cur_new, capital=float(cap_new),
                               slots=int(slots_new),
                               benchmarks={str(r.label).strip(): str(r.ticker).strip()
                                           for r in bed.itertuples() if str(r.label).strip() and str(r.ticker).strip()})
                    if save_data(data, version, f"{cfg['name']}: settings"):
                        _cached_closes.clear()
                        st.rerun()

            st.divider()
            st.markdown("**Telegram**")
            if not tg_token:
                st.info("Add this to the app's Secrets (share.streamlit.io → ⋮ → Settings → Secrets), then reboot:"
                        "\n\n```\n[telegram]\nbot_token = \"PASTE-BOT-TOKEN\"\n```")
            else:
                st.caption(f"Group: {cfg.get('telegram_chat_title', chat_id) if chat_id else 'not connected'}. "
                           "Detect lists the groups the bot has seen — if yours isn't listed, send "
                           "/start@<bot name> in the group and detect again.")
                c = st.columns(3)
                if c[0].button("Detect group"):
                    try:
                        st.session_state["tg_chats"] = notify.recent_group_chats(tg_token)
                    except Exception as e:
                        st.error(f"Telegram: {e}")
                if chat_id and c[1].button("Send a test message") and tg_send(f"Test from {name} tracker ✅"):
                    st.success("Test sent — check the group.")
                found = st.session_state.get("tg_chats")
                if found == []:
                    st.warning("No groups found yet. Send /start in the group, wait a few seconds, detect again.")
                elif found:
                    opts = {c_["id"]: c_["title"] for c_ in found}
                    gid = st.selectbox("Group", list(opts), format_func=opts.get)
                    if st.button("Use this group", type="primary"):
                        cfg.update(telegram_chat_id=gid, telegram_chat_title=opts[gid])
                        if save_data(data, version, f"{name}: telegram group"):
                            st.session_state.pop("tg_chats", None)
                            st.rerun()
                url = st.text_input("App link to include in messages", value=cfg.get("app_url") or "",
                                    placeholder="https://your-app.streamlit.app")
                if st.button("Save link") and url.strip() != (cfg.get("app_url") or ""):
                    cfg["app_url"] = url.strip() or None
                    if save_data(data, version, f"{name}: app link"):
                        st.rerun()

        with m5:
            st.caption("For a second model portfolio (e.g. the US one). Each portfolio has its own settings and lots.")
            new_portfolio_form("new_portfolio")
