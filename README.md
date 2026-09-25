# Model Portfolio Tracker

A small shared dashboard for the group's model portfolios: holdings, weekly
entries and exits, returns against benchmarks, and trade analytics. Anyone with
the link can view it. Editing is done on the page itself, behind an optional PIN.

- **Prices:** end-of-day closes from yfinance (`.NS`, with an automatic `.BO`
  fallback for BSE-only stocks). They're cached for 4 hours, and the sidebar
  button refreshes them.
- **Data:** `data/portfolio.json` in this repo. Every save from the page is a
  commit, so the repo history is the audit trail.
- **Portfolios:** several are supported (e.g. the Indian and US ones), each with
  its own capital, equal-weight slots, currency and benchmarks.

## One-time setup (about 15 minutes)

1. **Create the repo.** On github.com: New repository → name `model-portfolio`
   → Private → leave "Add a README" unticked → Create.
2. **Push these files** from Terminal (one command at a time):
   ```
   cd ~/LocalProjects/model_portfolio
   git init
   git add .
   git commit -m "initial-model-portfolio-tracker"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/model-portfolio.git
   git push -u origin main
   ```
3. **Create a token for saving.** On github.com: Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token.
   - Repository access: *Only select repositories* → `model-portfolio`.
   - Permissions → Repository permissions → **Contents: Read and write**.
   - Expiration: the longest offered. Copy the token.
4. **Deploy.** Go to share.streamlit.io and sign in with GitHub → Create app →
   pick the repo, branch `main`, main file `app.py`. Under Advanced settings →
   Secrets, paste the contents of `.streamlit/secrets.toml.example` with your
   token, repo name and PIN filled in → Deploy.
5. **Share.** In the app's settings, open *Sharing*. Make sure anyone with the
   link can view, then send the URL to the group.

## Weekly routine

Open the app → sidebar → enter the edit PIN → **Manage**:
- **Add entry:** symbol (e.g. `HFCL`; `.NS` is added automatically for INR
  portfolios; use `543210.BO` for BSE-only), price and date. Quantity defaults
  to equal weight (portfolio value ÷ slots). Tick *Override quantity* to set it
  yourself.
- **Close position:** pick the stock, then enter the exit price and date. A
  quantity below the full holding is a partial exit.
- **Edit lots:** a spreadsheet-style grid for fixing anything.
  - *Manual price* covers a stock Yahoo doesn't price.
- **Settings:** capital, slots, currency and benchmarks.
- **New portfolio:** Phase 2 (US).

If a save says the portfolio "was changed elsewhere", reload the page and redo
that one change. It means two edits overlapped.

## Notes
- Accounting uses fixed capital: value = capital + realised P&L + open-position
  P&L. That is the same method the previous portal used, and it reconciles with
  it to the rupee.
- Stock splits aren't adjusted automatically. After a split, fix that lot's
  quantity and entry price in *Edit lots*.
- If Yahoo is unavailable, the app shows a warning and falls back to manual or
  entry prices rather than failing.
- To run it locally: `pip install -r requirements.txt` then `streamlit run app.py`.
  Without secrets, it reads and writes the local `data/portfolio.json`.
