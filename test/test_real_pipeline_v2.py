"""
Same test as before, but every print is flushed immediately and each
step is announced BEFORE it runs - so if this hangs, you'll see
exactly which line it's stuck on, instead of silence.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

import sys
from datetime import date

def p(msg):
    print(msg, flush=True)

p("=" * 70)
p("Starting test_real_pipeline_v2.py")
p("=" * 70)

p("\n[1/7] Importing modules...")
from core.database import Database
from market_data.bhavcopy import BhavcopyScraper
from market_data.iv_updater import compute_atm_iv, update_iv_for_symbol
from market_data.ohlc_updater import update_ohlc_for_symbol
p("      done.")

TARGET = date(2026, 6, 25)
TEST_SYMBOLS = ["RELIANCE", "SBIN", "HDFCBANK", "NIFTY"]

p(f"\n[2/7] Creating BhavcopyScraper...")
scraper = BhavcopyScraper()
p("      done.")

p(f"\n[3/7] Downloading {TARGET} via scraper.download_date()...")
p("      (this does a priming request to nseindia.com, then the actual")
p("       file download - if it hangs here, that's the network step)")
success = scraper.download_date(TARGET)
p(f"      -> {'SUCCESS' if success else 'FAILED'}")
if not success:
    p("Stopping here - download itself failed even though the diagnostic")
    p("script showed HTTP 200 earlier. Possible rate-limiting on repeat")
    p("requests. Try waiting a minute and running again.")
    sys.exit(1)

p(f"\n[4/7] Parsing and normalizing columns (pandas read_csv on a ~180k row file)...")
df = scraper.load_fo(TARGET)
p(f"      -> {len(df)} rows, columns: {list(df.columns)}")

p(f"\n[5/7] Checking spot prices for {TEST_SYMBOLS}...")
for symbol in TEST_SYMBOLS:
    spot = scraper.get_spot_price(symbol, TARGET)
    p(f"      {symbol}: spot={spot}")

p(f"\n[6/7] Computing ATM IV for {TEST_SYMBOLS}...")
for symbol in TEST_SYMBOLS:
    iv = compute_atm_iv(scraper, symbol, TARGET)
    p(f"      {symbol}: iv={iv}")

p(f"\n[7/7] Connecting to database and writing IV/OHLC history...")
db = Database()
p(f"      DB mode: {'mock' if db.is_mock else 'real MongoDB'}")
for symbol in TEST_SYMBOLS:
    iv_result = update_iv_for_symbol(db, scraper, symbol, TARGET)
    ohlc_result = update_ohlc_for_symbol(db, scraper, symbol, TARGET)
    p(f"      {symbol}: iv_atm={iv_result['iv_atm']}  ivr={iv_result['ivr']}  ohlc_close={ohlc_result['close'] if ohlc_result else None}")

p("\n" + "=" * 70)
p("DONE - if you see this line, everything completed successfully.")
p("=" * 70)
