# Stock Entry

Scans a list of NSE stocks with daily prices from Yahoo Finance and shows entry signals
(breakout, pullback, first up day), CCI / RSI screens and MRI SPV / PV zones, with a chart per stock.

It also runs the swing rules of `TradingView.ipynb` on 2-hour candles: TradingView 2-hour bars (latest 500, via
`tvDatafeed`, `stock_entry/tradingview.py`); set `TV_USERNAME` / `TV_PASSWORD` (environment, GitHub Actions secrets,
Streamlit secrets), or in the local app click **TradingView** in the header and sign in there (kept in memory
only, `stock_entry/tv_login.py`; the live Streamlit site refuses page logins because every visitor shares it).
There is no Yahoo fallback: without them, or for a stock TradingView misses, the 2-hour fields
stay empty. Signals: HH / HL / LH / LL labels, LL→LL and LL→HL angles, **3 LL then HH**,
**4 LL then HH** and **LH breakout** (`stock_entry/swings.py`, `stock_entry/intraday.py`). A swing label is
confirmed 5 bars after its pivot, so these lists show signals from the last 20 two-hour bars.

**2-hour bars from the notebook:** when `TBC_all_stocks_bars.csv` (written by the loop cell at the end of
`TradingView.ipynb`) is in the project folder, the scan and the stock detail take their 2-hour bars from it
first (`stock_entry/two_hour_csv.py`); stocks it does not list (the notebook skips financial stocks) still come
from TradingView. To update the live site, re-run the notebook loop and push the CSV: the **Apply 2-hour CSV**
workflow then recomputes the 2-hour fields of the saved scan (`python -m stock_entry.apply_two_hour_csv`, no
downloads) and commits it. `TBC_all_stocks_summary.csv` is the notebook's one-row-per-stock view of the same data.

**Daily bars on disk:** `data/daily_bars.csv` keeps the latest 500 daily bars (about 2 years) of every
stock (`stock_entry/daily_store.py`). The first scan downloads the full history from Yahoo; after that a scan
asks only for the days it is missing (5 days to a few months, grouped by how far behind each stock is),
replaces days it already had (a bar fetched during market hours is completed later), appends the new ones
and drops the same number of oldest days. When a past close no longer matches (split / bonus issue) that
stock is downloaded again in full. The Daily scan workflow commits the file with the scan.

**TradingView sign-in and the captcha:** TradingView often answers a script's password sign-in with
"Please confirm that you are not a robot" (`recaptcha_required`), and the data then comes without login
(the latest bar of some stocks is missing). Use your browser session instead: sign in on tradingview.com
in the browser, open DevTools -> Application -> Cookies -> `https://www.tradingview.com`, and copy the
values of `sessionid` and `sessionid_sign` into `.env` (gitignored):

    TV_SESSIONID = "..."
    TV_SESSIONID_SIGN = "..."

(also as GitHub Actions secrets / Streamlit secrets for the daily scan and the live site). The session is
used first; `TV_USERNAME` / `TV_PASSWORD` only when it is missing or expired. Never commit these values.

**Day-wise update** (no full 500-bar download): after market close run

    python -m stock_entry.update_two_hour_csv --bars 4     # today's 4 bars; default 12 also fills a missed day

(or the last cell of `TradingView.ipynb`). It fetches the latest bars of every stock in the CSV, replaces
bars with the same time, appends new ones, keeps the latest 500 per stock, recomputes the notebook's signal
columns, rebuilds `TBC_all_stocks_summary.csv` and updates the saved scan. Signs in with `TV_USERNAME` /
`TV_PASSWORD` when set. If fewer than half the stocks come back, the files are left unchanged. Then:

    git add TBC_all_stocks_bars.csv TBC_all_stocks_summary.csv data/scan_latest.json
    git commit -m "chore: 2-hour bars for <date>" && git push

The same swing rules also run on the daily bars of the scan (latest 500, fields prefixed `d1_`): the
**TBC Zone Entry (1D)** list and a "Swings (1-day bars)" table in the stock detail.

## Run on Streamlit Community Cloud

1. Push this repository to GitHub.
2. On https://share.streamlit.io choose **Create app**, pick the repository and branch,
   and set the main file to `streamlit_app.py`.
3. Open the app. It shows the scan saved in `data/`.

### Daily scan

`.github/workflows/daily-scan.yml` runs the scan every weekday at 16:00 IST (after the NSE close)
and commits `data/scan_latest.json` and `data/fundamentals.json`. Streamlit Cloud redeploys on that
commit, so the app always opens with fresh data, even after a restart. Start it by hand from the
repository's **Actions** tab -> **Daily scan** -> **Run workflow**. Locally: `python -m stock_entry.scan_job`.

Pull before you push (`git pull --rebase`): the bot commits to `main` every weekday.

No API keys or secrets are needed.

## Run locally

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\streamlit run streamlit_app.py      # Streamlit version
.venv\Scripts\python run.py                       # or the Flask version at http://127.0.0.1:8765
```

Tests: `.venv\Scripts\python -m pytest --cov=stock_entry`

## Layout

- `stock_entry/` - signals, indicators, scanner, Flask API (`app.py`) and the Streamlit bridge (`bridge.py`)
- `web/` - the page (plain HTML / CSS / JS); `api.streamlit.js` replaces `api.js` under Streamlit
- `Final_data_greater_1000_debt_0.02.csv` - the stock list (all sectors scanned); `tickers.csv` - fallback list and company names
- `tests/` - pytest suite

Not investment advice.
