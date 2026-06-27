"""
Full pipeline test against REAL downloaded NSE data — not synthetic.
Confirms: the actual BhavcopyScraper class (not a manual replica),
column normalization against truly live data, IV/IVR computation,
and OHLC tracking, all using the real 25 Jun 2026 Bhavcopy.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from datetime import date
from core.database import Database
from market_data.bhavcopy import BhavcopyScraper
from market_data.iv_updater import compute_atm_iv, update_iv_for_symbol
from market_data.ohlc_updater import update_ohlc_for_symbol

TARGET = date(2026, 6, 25)
TEST_SYMBOLS = ["RELIANCE", "SBIN", "HDFCBANK", "NIFTY"]

print("=" * 70)
print(f"  Real pipeline test — {TARGET}")
print("=" * 70)

scraper = BhavcopyScraper()
print(f"\nDownloading via the REAL scraper.download_date()...")
success = scraper.download_date(TARGET)
print(f"  -> {'SUCCESS' if success else 'FAILED'}")
if not success:
    print("Stop here and tell me what happened - this should have worked")
    print("given the diagnostic already confirmed a 200 response.")
    raise SystemExit(1)

print(f"\nParsing and normalizing columns...")
df = scraper.load_fo(TARGET)
print(f"  -> {len(df)} total rows")
print(f"  -> Normalized columns: {list(df.columns)}")

print(f"\n--- Per-symbol checks ---")
for symbol in TEST_SYMBOLS:
    spot = scraper.get_spot_price(symbol, TARGET)
    rows = scraper.load_for_symbol(symbol, TARGET)
    n_rows = len(rows) if rows is not None else 0
    print(f"\n  {symbol}: spot={spot}  option_rows={n_rows}")
    if spot is None or n_rows == 0:
        print(f"    ⚠️  No data found for {symbol} - check the symbol name is correct")
        continue

    iv = compute_atm_iv(scraper, symbol, TARGET)
    print(f"    ATM IV: {iv}")

db = Database()  # uses your real Mongo if configured, mock otherwise
print(f"\n--- IV/IVR + OHLC, written to {'real MongoDB' if not db.is_mock else 'mock (not persisted)'} ---")
for symbol in TEST_SYMBOLS:
    iv_result = update_iv_for_symbol(db, scraper, symbol, TARGET)
    ohlc_result = update_ohlc_for_symbol(db, scraper, symbol, TARGET)
    print(f"  {symbol}: iv_atm={iv_result['iv_atm']}  ivr={iv_result['ivr']}  "
          f"(ivr=None is expected on first run - needs several days of history)")
    print(f"           ohlc={ohlc_result}")

print("\n" + "=" * 70)
print("If all symbols above show real spot/IV numbers (not None/0 rows),")
print("the entire Step 5 pipeline is confirmed working against live data.")
print("=" * 70)
