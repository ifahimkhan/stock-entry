# Stock Entry

Scans a list of NSE stocks with daily prices from Yahoo Finance and shows entry signals
(breakout, pullback, first up day), CCI / RSI screens and MRI SPV / PV zones, with a chart per stock.

It also runs the swing rules of `TradingView.ipynb` on 2-hour candles: TradingView 2-hour bars (latest 500, via
`tvDatafeed`, `stock_entry/tradingview.py`) when `TV_USERNAME` / `TV_PASSWORD` are set, otherwise Yahoo hourly bars
combined from the 09:15 open. Stocks TradingView misses use Yahoo, and each stock shows its source. Signals: HH / HL / LH / LL labels, LL→LL and LL→HL angles, **3 LL then HH**,
**4 LL then HH** and **LH breakout** (`stock_entry/swings.py`, `stock_entry/intraday.py`). A swing label is
confirmed 5 bars after its pivot, so these lists show signals from the last 20 two-hour bars.
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
