"""
notify.py -- Telegram notifications for the weekly portfolio changes.

The bot token lives in secrets ([telegram] bot_token). The group's chat id is
stored per portfolio in portfolio.json (so the US portfolio can post to a
different group later); the app's "Detect group" button finds it.

Messages use Telegram's HTML formatting, so every name is escaped.
"""

from __future__ import annotations

import html

import pandas as pd
import requests

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    r = requests.post(API.format(token=token, method=method), json=payload or {}, timeout=20)
    try:
        j = r.json()
    except ValueError:
        raise TelegramError(f"HTTP {r.status_code} from Telegram")
    if not j.get("ok"):
        raise TelegramError(j.get("description", f"HTTP {r.status_code}"))
    return j["result"]


def recent_group_chats(token: str) -> list[dict]:
    """Group chats the bot has seen recently (from getUpdates). A group only
    shows up after the bot was added or someone messaged it there."""
    chats = {}
    for u in _call(token, "getUpdates", {"allowed_updates": ["message", "my_chat_member"]}):
        for key in ("message", "my_chat_member", "edited_message"):
            chat = (u.get(key) or {}).get("chat")
            if chat and chat.get("type") in ("group", "supergroup"):
                chats[chat["id"]] = {"id": chat["id"], "title": chat.get("title", str(chat["id"]))}
    return list(chats.values())


def send_message(token: str, chat_id, text: str) -> None:
    _call(token, "sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                 "disable_web_page_preview": True})


def _pct(v) -> str:
    return "" if v is None or pd.isna(v) else f"{v:+.1f}%"


def _price(v) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def week_message(name: str, week: pd.Timestamp, changes: pd.DataFrame, stats: dict,
                 app_url: str | None = None, note: str | None = None) -> str:
    """changes: rows of core.weekly_changes() for one week."""
    e = html.escape
    sym = lambda s: e(str(s).split(".")[0])
    exits = changes[changes["action"] == "Exit"].sort_values("date")
    entries = changes[changes["action"] == "Entry"].sort_values("date")
    lines = [f"<b>{e(name)} — week of {week:%d %b %Y}</b>"]
    if note:
        lines += ["", e(note)]
    lines += ["", f"<b>Exits ({len(exits)})</b>"]
    lines += [f"• {sym(r.symbol)} @ {_price(r.price)}  ({_pct(r.pnl_pct)})" for r in exits.itertuples()] or ["• none"]
    lines += ["", f"<b>Entries ({len(entries)})</b>"]
    lines += [f"• {sym(r.symbol)} @ {_price(r.price)}" for r in entries.itertuples()] or ["• none"]
    summary = f"Portfolio {_pct(stats.get('total_return_pct'))} since start"
    bench = stats.get("bench") or {}
    if bench:
        summary += " · " + " · ".join(f"{e(k)} {_pct(v)}" for k, v in list(bench.items())[:2])
    lines += ["", summary]
    if app_url:
        lines.append(f'<a href="{e(app_url, quote=True)}">View portfolio</a>')
    return "\n".join(lines)


def preview_markdown(msg: str) -> str:
    """Render the Telegram HTML message as Streamlit markdown for the preview."""
    import re
    md = re.sub(r"<b>(.*?)</b>", r"**\1**", msg)
    md = re.sub(r'<a href="(.*?)">(.*?)</a>', r"[\2](\1)", md)
    md = html.unescape(md).replace("$", "\\$")
    return "  \n".join(md.split("\n"))


# ── plan / execution messages ─────────────────────────────────────────────
def _s(sym) -> str:
    return html.escape(str(sym).split(".")[0])


def plan_message(name: str, items: list[dict], note: str | None = None, app_url: str | None = None) -> str:
    """Evening message: the full plan for the next session."""
    e = html.escape
    exits = [i for i in items if i["action"] == "exit"]
    entries = [i for i in items if i["action"] == "entry"]
    lines = [f"<b>📋 {e(name)} — plan for next session</b>"]
    if note:
        lines += ["", e(note)]
    lines += ["", f"<b>Exit ({len(exits)})</b>"]
    lines += [f"• {_s(i['symbol'])}" + (f" — {e(i['note'])}" if i.get("note") else "") for i in exits] or ["• none"]
    lines += ["", f"<b>Enter ({len(entries)})</b>"]
    lines += [f"• {_s(i['symbol'])}" + (f" (ref {_price(i['ref_price'])})" if i.get("ref_price") else "")
              + (f" — {e(i['note'])}" if i.get("note") else "") for i in entries] or ["• none"]
    if app_url:
        lines += ["", f'<a href="{e(app_url, quote=True)}">View portfolio</a>']
    return "\n".join(lines)


def plan_diff(published: list[dict], items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(added, removed) between the last published plan and the current one,
    compared by (action, symbol)."""
    key = lambda i: (i["action"], i["symbol"])
    old, new = {key(i): i for i in published}, {key(i): i for i in items}
    return [new[k] for k in new if k not in old], [old[k] for k in old if k not in new]


def plan_update_message(name: str, added: list[dict], removed: list[dict], items: list[dict],
                        note: str | None = None) -> str:
    e = html.escape
    verb = lambda i: "exit" if i["action"] == "exit" else "enter"
    lines = [f"<b>🔁 {e(name)} — plan update</b>"]
    if note:
        lines += ["", e(note)]
    lines += [""]
    lines += [f"➕ {verb(i)} {_s(i['symbol'])}" + (f" — {e(i['note'])}" if i.get("note") else "") for i in added]
    lines += [f"➖ no longer {'exiting' if i['action'] == 'exit' else 'entering'} {_s(i['symbol'])}" for i in removed]
    ex = ", ".join(_s(i["symbol"]) for i in items if i["action"] == "exit") or "none"
    en = ", ".join(_s(i["symbol"]) for i in items if i["action"] == "entry") or "none"
    lines += ["", f"<b>Plan now</b> — exit: {ex} · enter: {en}"]
    return "\n".join(lines)


def executed_message(name: str, ex: dict, holdings: list[tuple], stats: dict, app_url: str | None = None) -> str:
    """Morning message. ex = the execution record saved by the app; holdings =
    [(symbol, pnl_pct)] of all open positions after execution."""
    e = html.escape
    d = pd.Timestamp(ex["date"])
    lines = [f"<b>✅ {e(name)} — executed {d:%d %b %Y}</b>"]
    if ex.get("exits"):
        lines += ["", f"<b>Exited ({len(ex['exits'])})</b>"]
        lines += [f"• {_s(x['symbol'])} @ {_price(x['price'])}" + (f" × {x['qty']:g}" if x.get("qty") else "")
                  + f"  ({_pct(x.get('pnl_pct'))})" for x in ex["exits"]]
    if ex.get("entries"):
        lines += ["", f"<b>Entered ({len(ex['entries'])})</b>"]
        lines += [f"• {_s(x['symbol'])} @ {_price(x['price'])} × {x['qty']:g}" for x in ex["entries"]]
    if ex.get("replaced"):
        lines += ["", "<b>Changed from plan</b>"]
        lines += [f"• {_s(r['from'])} → {_s(r['to'])}" + (f" — {e(r['reason'])}" if r.get("reason") else "")
                  for r in ex["replaced"]]
    if ex.get("skipped"):
        lines += ["", "<b>Not done (carried to next plan)</b>"]
        lines += [f"• {'exit' if k['action'] == 'exit' else 'enter'} {_s(k['symbol'])}"
                  + (f" — {e(k['reason'])}" if k.get("reason") else "") for k in ex["skipped"]]
    lines += ["", f"<b>Portfolio now ({len(holdings)})</b>"]
    lines += [", ".join(f"{_s(sym)} {_pct(p)}" for sym, p in holdings) or "none"]
    lines += ["", f"Portfolio {_pct(stats.get('total_return_pct'))} since start"]
    if app_url:
        lines.append(f'<a href="{e(app_url, quote=True)}">View portfolio</a>')
    return "\n".join(lines)


def no_change_message(name: str, holdings: list[tuple], stats: dict, note: str | None = None,
                      app_url: str | None = None) -> str:
    """Review done, nothing to change: say so, with the current portfolio."""
    e = html.escape
    lines = [f"<b>⏸️ {e(name)} — no changes this review</b>"]
    if note:
        lines += ["", e(note)]
    lines += ["", f"<b>Holding ({len(holdings)})</b>",
              ", ".join(f"{_s(sym)} {_pct(p)}" for sym, p in holdings) or "none"]
    summary = f"Portfolio {_pct(stats.get('total_return_pct'))} since start"
    bench = stats.get("bench") or {}
    if bench:
        summary += " · " + " · ".join(f"{e(k)} {_pct(v)}" for k, v in list(bench.items())[:2])
    lines += ["", summary]
    if app_url:
        lines.append(f'<a href="{e(app_url, quote=True)}">View portfolio</a>')
    return "\n".join(lines)
