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
