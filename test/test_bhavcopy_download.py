"""Quick manual test of a real Bhavcopy download."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # works from test/ or project root

from datetime import date, timedelta
from market_data.bhavcopy import BhavcopyScraper

scraper = BhavcopyScraper()

# Today might not have a published Bhavcopy yet (NSE usually publishes
# a few hours after market close) - try today first, fall back to
# yesterday if that fails.
target = date.today()
success = scraper.download_date(target)

if not success:
    print(f"No file for {target} yet (could be too early today, a weekend, or a holiday).")
    target = date.today() - timedelta(days=1)
    print(f"Trying {target} instead...")
    success = scraper.download_date(target)

if success:
    print(f"\n✅ Downloaded successfully for {target}")
    df = scraper.load_fo(target)
    print(f"Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    print(f"\nSample SBILIFE spot price: {scraper.get_spot_price('SBILIFE', target)}")
else:
    print(f"\n❌ Download failed for both {date.today()} and {target}.")
    print("Run scraper.diagnose() after a successful download to inspect the raw file,")
    print("or check the actual HTTP error - the download method swallows exceptions")
    print("into a log warning, so check your console output above for the real reason.")
