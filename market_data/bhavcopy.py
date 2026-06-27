"""
market_data/bhavcopy.py
───────────────────────────
Downloads and parses NSE F&O Bhavcopy (end-of-day settlement data).

THIS SCHEMA IS NOT GUESSED — it's the actual confirmed 2026 NSE F&O
Bhavcopy column layout, found by debugging a real downloaded file in
an earlier POC-06 spike (several iterations were needed; NSE's column
names don't match what their own historical docs describe):

  TckrSymb      = the underlying symbol, e.g. "SBILIFE" — NOT the full
                  option name (that's FinInstrmNm, e.g. "SBILIFE26MAY1500CE")
  UndrlygPric   = the underlying SPOT price — directly in the F&O file.
                  No separate equity/CM Bhavcopy download is needed at
                  all; this single file covers both option pricing and
                  the underlying's price history.
  FinInstrmTp   = "STO" (stock option) / "IDO" (index option) — NOT
                  "OPTSTK"/"OPTIDX" as NSE's own older documentation
                  describes.
  XpryDt        = "YYYY-MM-DD"
  StrkPric, OptnTp, ClsPric, SttlmPric, OpnIntrst — as named.

NSE BLOCKS PLAIN REQUESTS without browser-like headers and a primed
session (visiting the main site first to get cookies) — both handled
below.

COLUMN NORMALIZATION IS DEFENSIVE, NOT HARDCODED — NSE has changed
this format before and will again. Three-pass matching (exact ->
case-insensitive -> substring) means a future format change is
diagnosable via diagnose(), not a silent KeyError.

CANNOT BE TESTED IN THIS SANDBOX — archives.nseindia.com isn't on the
network allowlist here. This is built from confirmed real data found
in an earlier debugging session, but the actual download needs to be
tested on a machine with real internet access.

PROJECT PATH:  market_data/bhavcopy.py
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core.logging_config import setup_logging

logger = setup_logging(__name__)

DATA_DIR = Path("data/bhavcopy")

URL_FO = "https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
}

# Canonical name -> list of actual column names NSE has used (confirmed +
# defensive extras for format changes). TckrSymb listed first since
# that's the CONFIRMED correct one for the current format.
COLUMN_MAP = [
    ("SYMBOL",      ["TckrSymb", "SYMBOL", "UndrlygScty", "Undrlyng", "Symbol"]),
    ("INSTRUMENT",  ["FinInstrmTp", "INSTRUMENT", "Instrument"]),
    ("EXPIRY_DT",   ["XpryDt", "EXPIRY_DT", "ExpiryDate", "ExpiryDt"]),
    ("STRIKE_PR",   ["StrkPric", "STRIKE_PR", "StrikePrice"]),
    ("OPTION_TYP",  ["OptnTp", "OPTION_TYP", "OptionType"]),
    ("CLOSE",       ["ClsPric", "CLOSE", "SttlmPric", "ClsePric"]),
    ("SETTLE_PR",   ["SttlmPric", "SETTLE_PR"]),
    ("SPOT",        ["UndrlygPric"]),
    ("OPEN_INT",    ["OpnIntrst", "OPEN_INT", "OpenInterest"]),
]

STOCK_OPTION_TYPES = {"STO", "OPTSTK"}
INDEX_OPTION_TYPES = {"IDO", "OPTIDX"}


class BhavcopyScraper:
    """Downloads and parses NSE F&O Bhavcopy data."""

    def __init__(self, data_dir: str | Path = DATA_DIR) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._session = None
        self._primed = False
        self._col_logged = False

    # ── Public API ───────────────────────────────────────────────────

    def diagnose(self) -> None:
        """
        Print actual column names + a sample row from the most recently
        downloaded file. Run this first if NSE changes their format
        again and parsing breaks — tells you exactly what to fix in
        COLUMN_MAP rather than guessing from a KeyError.
        """
        import pandas as pd

        files = sorted(self._data_dir.glob("fo_*.csv"))
        if not files:
            print("No F&O files in data/bhavcopy/. Run download_date() first.")
            return
        df = pd.read_csv(files[-1], nrows=3)
        print(f"\nDIAGNOSTIC — {files[-1].name}")
        print(f"Columns ({len(df.columns)}):")
        for c in df.columns:
            print(f"  {c!r}")
        print("\nFirst row:")
        for col, val in df.iloc[0].items():
            print(f"  {col:<28} = {val!r}")

    def download_date(self, dt: date) -> bool:
        """Download one date's F&O Bhavcopy. Returns True on success."""
        self._prime_session()
        return self._download_one(dt)

    def download_range(self, start: date, end: date, delay_secs: float = 1.5) -> list[date]:
        """Download every trading day's Bhavcopy in [start, end] (skips weekends AND holidays)."""
        import time

        from core.nse_holidays import is_trading_day

        self._prime_session()
        downloaded = []
        current = start
        while current <= end:
            if is_trading_day(current):
                if self._download_one(current):
                    downloaded.append(current)
                time.sleep(delay_secs)
            current += timedelta(days=1)
        return downloaded

    def load_fo(self, dt: date):
        """Load one date's Bhavcopy as a normalized DataFrame, or None if not downloaded."""
        import pandas as pd

        path = self._fo_path(dt)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, low_memory=False)
            df = self._normalize(df)
            return df
        except Exception as e:
            logger.warning("Failed to parse Bhavcopy %s: %s", path.name, e)
            return None

    def load_for_symbol(
        self, symbol: str, dt: date,
        expiry: Optional[date] = None, option_type: Optional[str] = None,
    ):
        """Load Bhavcopy rows for one underlying symbol, optionally filtered."""
        df = self.load_fo(dt)
        if df is None or "SYMBOL" not in df.columns:
            return None
        df = df[df["SYMBOL"].str.strip() == symbol.strip()]
        if "INSTRUMENT" in df.columns:
            df = df[df["INSTRUMENT"].isin(STOCK_OPTION_TYPES | INDEX_OPTION_TYPES)]
        if expiry is not None and "EXPIRY_DT" in df.columns:
            df = df[df["EXPIRY_DT"].dt.date == expiry]
        if option_type and "OPTION_TYP" in df.columns:
            df = df[df["OPTION_TYP"] == option_type]
        return df.reset_index(drop=True)

    def get_spot_price(self, symbol: str, dt: date) -> Optional[float]:
        """
        Spot price for a symbol on a date — directly from the F&O
        file's SPOT (UndrlygPric) column. No separate equity Bhavcopy
        download needed.
        """
        df = self.load_for_symbol(symbol, dt)
        if df is None or df.empty or "SPOT" not in df.columns:
            return None
        spot = df["SPOT"].dropna()
        return float(spot.iloc[0]) if not spot.empty and spot.iloc[0] > 0 else None

    def list_downloaded_dates(self) -> list[date]:
        dates = []
        for p in sorted(self._data_dir.glob("fo_????????.csv")):
            try:
                dates.append(datetime.strptime(p.stem.split("_")[1], "%Y%m%d").date())
            except Exception:
                pass
        return dates

    # ── Private: column normalization (3-pass, defensive) ────────────

    def _normalize(self, df):
        import pandas as pd

        if not self._col_logged:
            logger.info("Bhavcopy raw columns: %s", list(df.columns))
            self._col_logged = True

        df = self._apply_column_map(df)

        if "SYMBOL" in df.columns:
            df["SYMBOL"] = (
                df["SYMBOL"].astype(str).str.strip()
                .str.replace(r"-EQ$", "", regex=True)
                .str.replace(r"-BE$", "", regex=True)
            )
        if "EXPIRY_DT" in df.columns:
            df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"], errors="coerce")
        for col in ["STRIKE_PR", "CLOSE", "SETTLE_PR", "SPOT", "OPEN_INT"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "OPTION_TYP" in df.columns:
            df["OPTION_TYP"] = df["OPTION_TYP"].astype(str).str.strip().str.upper()

        return df

    @staticmethod
    def _apply_column_map(df):
        cols_lower = {c.lower(): c for c in df.columns}
        rename, used = {}, set()

        for canonical, aliases in COLUMN_MAP:
            if canonical in df.columns:
                continue
            found = False
            for alias in aliases:
                if alias in df.columns and alias not in used:
                    rename[alias] = canonical
                    used.add(alias)
                    found = True
                    break
            if found:
                continue
            for alias in aliases:
                key = alias.lower()
                if key in cols_lower and cols_lower[key] not in used:
                    rename[cols_lower[key]] = canonical
                    used.add(cols_lower[key])
                    found = True
                    break
            if found:
                continue
            for alias in aliases:
                key = alias.lower()
                if len(key) < 5:
                    continue
                for col_l, col_actual in cols_lower.items():
                    if key in col_l and col_actual not in used:
                        rename[col_actual] = canonical
                        used.add(col_actual)
                        found = True
                        break
                if found:
                    break

        return df.rename(columns=rename)

    # ── Private: download ──────────────────────────────────────────

    def _prime_session(self) -> None:
        if self._primed:
            return
        import requests

        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        try:
            self._session.get("https://www.nseindia.com", timeout=10)
            self._primed = True
        except Exception as e:
            logger.warning("Could not prime NSE session (may get 403): %s", e)

    def _download_one(self, dt: date) -> bool:
        dest = self._fo_path(dt)
        if dest.exists():
            return True
        url = URL_FO.format(date=dt.strftime("%Y%m%d"))
        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.debug("Bhavcopy %s: HTTP %d (likely weekend/holiday)", dt, resp.status_code)
                return False
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                content = zf.open(zf.namelist()[0]).read()
            dest.write_bytes(content)
            logger.info("Saved Bhavcopy %s (%d KB)", dt, len(content) // 1024)
            return True
        except Exception as e:
            logger.warning("Bhavcopy download failed for %s: %s", dt, e)
            return False

    def _fo_path(self, dt: date) -> Path:
        return self._data_dir / f"fo_{dt.strftime('%Y%m%d')}.csv"
