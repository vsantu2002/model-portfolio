# Model Portfolio Tracker

A shared dashboard for the group's model portfolios. It shows holdings, the
plan for the next session, executed trades, returns against benchmarks and
trade analytics, and it posts updates to a Telegram group. Anyone with the
link can view it. Changes are made on the page itself, after entering the
edit PIN.

- **Live app:** https://alpha-mi20.streamlit.app
- **Prices:** end-of-day closes from yfinance (`.NS` first, with an automatic
  `.BO` fallback for BSE-only stocks). During market hours it shows the price so
  far, labelled "intraday". Prices are cached for 4 hours; **↻ Refresh prices**
  in the sidebar forces a fresh pull.
- **Splits and bonuses:** Yahoo rewrites a stock's past prices after a
  split or bonus. The app reads Yahoo's split records and checks each lot to
  see which basis it was recorded on, so P&L, history and "since exit" stay
  correct.
- **Data:** `data/portfolio.json` in this repo. Every change saved from the page
  is a commit, so the repo history is the full audit trail, and any mistake
  can be rolled back.
- **Portfolios:** several are supported (e.g. Mi20 in INR, a US one in
  USD). Each has its own capital, equal-weight slots, benchmarks and Telegram
  group.

## Routine (after entering the edit PIN → Manage)

**Evening: Plan**
1. **Exit:** pick one or more holdings. **Enter:** type one or more symbols
   (`HFCL, BEML, 543210.BO`). `.NS` is added automatically for INR portfolios.
   The reason box is optional.
2. Check the preview → **Publish plan**. That posts 📋 to the group, and
   everyone also sees the plan on the Overview.
3. Plan changed before the open? Edit it and click **Publish update**. Only
   the changes are sent (🔁 ➕/➖).
4. Holding everything? With the plan empty, open **⏸️ No changes this review**
   → **Post 'No changes'**. It posts the current holdings and performance.

**Morning: Execute** (works on any date)
1. Pick the execution date (defaults to today) and enter each actual **fill price**.
2. Couldn't buy a stock (e.g. upper circuit)? Type the **replacement symbol**
   in that row, with its fill price and a reason. The message reports it as
   "Changed from plan".
3. Couldn't do something at all? **Untick Done.** It stays in the plan for
   the next day.
4. **Qty:** blank means equal weight for entries (portfolio value ÷ slots),
   or the whole position for exits. A smaller exit quantity is a partial exit.
5. **Confirm execution** → **Send executed message**. That posts ✅ with the
   fills, any changes and skips, and the full list of open positions.

**Other tabs**
- **Add entry / Close position:** record a single trade directly, with any date.
- **Edit lots:** a spreadsheet-style grid for fixing anything.
  *Manual price* covers a stock Yahoo doesn't price.
- **Weekly summary:** an optional recap of a whole week's trades.
- **Settings:** name, currency, capital, slots and benchmarks, plus
  **Telegram** (detect group, test message, app link).
- **New portfolio:** e.g. the US portfolio (Phase 2).

## One-time setup

### Secrets
Streamlit app → ⋮ → **Settings → Secrets**:

```
[github]
token  = "github_pat_..."          # fine-grained token: this repo only, Contents = Read and write
repo   = "vsantu2002/model-portfolio"
branch = "main"
path   = "data/portfolio.json"

[app]
edit_pin = "...."                  # remove this section to let anyone with the link edit

[telegram]
bot_token = "123456:ABC..."        # from @BotFather
```

After changing Secrets, **reboot the app** (Manage app → ⋮ → Reboot app).

### Telegram group (per portfolio)
1. Create a bot with **@BotFather** (`/newbot`), put its token in Secrets, and
   add the bot to the group.
2. In the group, send `/start@<bot_username>`. Bots only see messages
   addressed to them.
3. In the app, go to **Settings → Telegram → Detect group** → pick it → **Use this group**.
4. Paste the app's address into **App link** → **Save link** → **Send a test message**.

For the US portfolio: create it under **New portfolio**. Then repeat step 3,
choosing that portfolio in the sidebar first, to connect its own group. One
bot can serve several groups.

### Deploying from scratch
1. Create a GitHub repo and push these files.
2. Create the fine-grained token (github.com/settings/personal-access-tokens/new).
3. On share.streamlit.io: Create app → this repo, branch `main`, file `app.py`.
   Paste the Secrets → Deploy.
4. Keep the repo **public**, so friends can view the app without an account.
   The secrets are never in the repo.

## Updating the code
One command at a time, from `~/LocalProjects/model_portfolio`:
```
git pull
mv ~/Downloads/<changed files> . && git add <changed files> && git commit -m "short-description" && git push
```
- **Always `git pull` first.** Saves from the app are commits your Mac doesn't have yet.
- **Push all changed files in one go.** If a change doesn't take effect,
  **Reboot app**: it clears old code and cached prices.

## Notes
- **Accounting:** fixed capital. Value = capital + realised P&L + open-position
  P&L, the same method as the previous portal, and it reconciles with it to
  the rupee.
- **Monthly returns:** "Year" compounds the months; it doesn't add them. The
  first year is partial and the current year is year-to-date. CAGR (annualised)
  is on the Overview.
- **Free hosting:** the app sleeps after a stretch with no visitors. The first
  visit afterwards takes about 30 seconds to wake it. Data is never affected.
- **Missing prices:** if Yahoo is unavailable or misses a stock, the app
  warns and falls back to that lot's manual price, then its entry price.
- **Running locally:** `pip install -r requirements.txt` then `streamlit run app.py`.
  Without secrets, it uses the local `data/portfolio.json`.
