"""
dashboard/strangle_grouper.py
────────────────────────────────
Parses live Kite positions (potentially across MULTIPLE broker
connections for one user), groups CE + PE legs into strangle pairs,
and computes strangle-level P&L, distance-from-spot status, and
delta-neutrality. The symbol-parsing logic is the proven POC-10
pattern; this version adds multi-connection isolation and per-leg
Black-Scholes delta (dashboard/greeks.py).

GROUPING KEY: (connection_id, underlying, expiry) — two different
Kite accounts holding the same underlying+expiry strangle are NEVER
merged into one, since they are genuinely separate positions.

OFFICIALLY STEP 9'S FOLDER — pulled forward for Step 2.1's live
dashboard. Step 9 will extend this with the dashboard/pages split;
this file's contract is designed to stay stable when that happens.

PROJECT PATH:  dashboard/strangle_grouper.py
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


# ── Known NSE F&O underlyings (longest first for safe prefix matching) ────
# IMPORTANT: Keep sorted longest-first so longer names (e.g. ASIANPAINT)
# are matched before shorter prefixes that might be substrings.
KNOWN_UNDERLYINGS = sorted([
    # Indices
    "BANKNIFTY", "NIFTY", "MIDCPNIFTY", "FINNIFTY", "NIFTYNXT50",
    # Large-cap equity F&O (NSE permitted list)
    "HDFCBANK", "TCS", "RELIANCE", "INFY", "SBIN", "ICICIBANK",
    "AXISBANK", "KOTAKBANK", "ITC", "HINDUNILVR", "BAJFINANCE",
    "TATASTEEL", "SUNPHARMA", "WIPRO", "NESTLEIND", "SBILIFE",
    "DRREDDY", "CIPLA", "ADANIPORTS", "POWERGRID", "NTPC",
    # Additional common F&O stocks
    "ASIANPAINT", "BAJAJFINSV", "BHARTIARTL", "BPCL", "BRITANNIA",
    "COALINDIA",  "DIVISLAB",  "EICHERMOT",  "GRASIM",  "HCLTECH",
    "HEROMOTOCO", "HINDALCO",  "INDUSINDBK", "JSWSTEEL",  "LT",
    "M&M",        "MARUTI",    "ONGC",       "SBICARD",   "SHREECEM",
    "TATAMOTORS", "TATACONSUM","TECHM",      "TITAN",     "TRENT",
    "ULTRACEMCO", "UPL",       "VEDL",       "ZOMATO",    "BAJAJ-AUTO",
    "APOLLOHOSP", "ADANIENT",  "PIDILITIND", "CHOLAFIN",  "MUTHOOTFIN",
], key=len, reverse=True)

KITE_SPOT_MAP = {
    # Indices — special Kite instrument strings
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "NIFTY":      "NSE:NIFTY 50",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "NIFTYNXT50": "NSE:NIFTY NEXT 50",
    # Equity stocks use NSE:SYMBOL format — added lazily for mock spots below.
    # Any equity NOT listed here is auto-resolved to NSE:<SYMBOL> in the
    # spot-price fetch code in pages/1_Live_Dashboard.py.
}

MOCK_SPOTS = {
    # Indices
    "BANKNIFTY":  51_250.0,
    "NIFTY":      24_500.0,
    "MIDCPNIFTY": 12_800.0,
    "FINNIFTY":   23_500.0,
    "NIFTYNXT50": 68_000.0,
    # Equity mock spots (used when no real Kite connection available)
    "ASIANPAINT": 2_450.0,
    "HDFCBANK":   1_780.0,
    "RELIANCE":   2_980.0,
    "INFY":       1_620.0,
    "TCS":        3_650.0,
    "SBIN":         840.0,
    "ICICIBANK":  1_350.0,
    "AXISBANK":   1_190.0,
    "KOTAKBANK":  2_100.0,
    "BAJFINANCE": 6_800.0,
    "BHARTIARTL": 1_850.0,
    "MARUTI":    12_500.0,
    "TITAN":      3_300.0,
    "LT":         3_700.0,
    "TATAMOTORS":   920.0,
    "HINDALCO":     700.0,
}

THRESHOLDS = {"CRITICAL": 3.0, "WARNING": 5.0, "CAUTION": 8.0}

# ── NSE F&O lot sizes ────────────────────────────────────────────────────
# ⚠️  NSE revises lot sizes periodically (roughly every 6 months) — these
# are best-effort values, NOT guaranteed current. ASIANPAINT=30 is
# confirmed directly from a real Kite position ("5 x 30" lots × size).
# Others are reasonable estimates — VERIFY against your own contract note
# or the latest NSE F&O lot size circular before relying on the
# lot-equivalent delta for real position-sizing decisions. Any symbol not
# listed here shows "N/A" for lot-equivalent delta rather than guessing.
LOT_SIZE_MAP: dict[str, int] = {
    # Indices
    "NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 25,
    "MIDCPNIFTY": 50, "NIFTYNXT50": 10,
    # Equities — verify before trusting for real sizing
    "ASIANPAINT": 250,   # confirmed: 1250 qty ÷ 5 lots, cross-checked against Sensibull
    "HDFCBANK": 550, "RELIANCE": 500, "TCS": 175, "INFY": 400,
    "SBIN": 750, "ICICIBANK": 700, "AXISBANK": 625, "KOTAKBANK": 400,
    "BAJFINANCE": 125, "BHARTIARTL": 475, "MARUTI": 50, "TITAN": 175,
    "LT": 150, "TATAMOTORS": 1425, "HINDALCO": 1400,
}

# Delta-neutral classification — a rough, ADJUSTABLE heuristic, not a
# precise rule. Different traders use different thresholds; this one
# expresses "neutral" as being within roughly one typical lot's worth
# of net delta exposure.
NEUTRAL_LOT_MULTIPLE = 1.0
SLIGHT_BIAS_LOT_MULTIPLE = 2.0
DEFAULT_LOT_SIZE_FOR_NEUTRALITY = 15


@dataclass
class ParsedOption:
    tradingsymbol:     str
    underlying:        str
    expiry:             str
    strike:             float
    option_type:        str
    quantity:           int
    avg_price:          float
    ltp:                float
    pnl:                float
    connection_id:      str = ""
    connection_label:   str = ""

    @property
    def abs_qty(self) -> int:
        return abs(self.quantity)

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def entry_value(self) -> float:
        return self.avg_price * self.abs_qty

    @property
    def delta_info(self) -> dict:
        """
        Black-Scholes-derived delta info. Computed lazily by
        Strangle.compute_deltas() (needs the strangle's spot price,
        which a single leg doesn't know on its own).
        """
        return getattr(self, "_delta_info", {"delta": None, "implied_vol_pct": None,
                                              "days_to_expiry": None, "converged": False})

    @property
    def position_delta(self) -> Optional[float]:
        """raw_delta × signed quantity — the full-scale exposure number."""
        d = self.delta_info.get("delta")
        return None if d is None else d * self.quantity

    @property
    def lot_size(self) -> Optional[int]:
        """Known NSE lot size for this leg's underlying, or None if unlisted."""
        return LOT_SIZE_MAP.get(self.underlying)

    @property
    def lot_equivalent_delta(self) -> Optional[float]:
        """
        position_delta ÷ lot_size — the Sensibull-comparable per-leg
        number ("this leg behaves like being short/long X lots of the
        underlying"), instead of the raw, larger-scale position_delta.
        Returns None if lot_size is unknown for this underlying.
        """
        pd = self.position_delta
        if pd is None or self.lot_size is None:
            return None
        return pd / self.lot_size


@dataclass
class Strangle:
    underlying:        str
    expiry:             str
    connection_id:      str = ""
    connection_label:   str = ""
    ce_legs:            list[ParsedOption] = field(default_factory=list)
    pe_legs:            list[ParsedOption] = field(default_factory=list)
    spot:               float = 0.0

    @property
    def is_complete(self) -> bool:
        return bool(self.ce_legs) and bool(self.pe_legs)

    @property
    def ce_legs_sorted(self) -> list[ParsedOption]:
        return sorted(self.ce_legs, key=lambda l: l.strike)

    @property
    def pe_legs_sorted(self) -> list[ParsedOption]:
        return sorted(self.pe_legs, key=lambda l: l.strike)

    @property
    def ce_strike(self) -> Optional[int]:
        return self.ce_legs[0].strike if self.ce_legs else None

    @property
    def pe_strike(self) -> Optional[int]:
        return self.pe_legs[0].strike if self.pe_legs else None

    @property
    def ce_ltp(self) -> float:
        return sum(l.ltp for l in self.ce_legs) / len(self.ce_legs) if self.ce_legs else 0.0

    @property
    def pe_ltp(self) -> float:
        return sum(l.ltp for l in self.pe_legs) / len(self.pe_legs) if self.pe_legs else 0.0

    @property
    def combined_ltp(self) -> float:
        return self.ce_ltp + self.pe_ltp

    @property
    def total_pnl(self) -> float:
        return sum(l.pnl for l in self.ce_legs + self.pe_legs)

    @property
    def ce_distance_pct(self) -> float:
        if not self.ce_strike or not self.spot:
            return 0.0
        return (self.ce_strike - self.spot) / self.spot * 100

    @property
    def pe_distance_pct(self) -> float:
        if not self.pe_strike or not self.spot:
            return 0.0
        return (self.spot - self.pe_strike) / self.spot * 100

    @property
    def worst_distance_pct(self) -> float:
        distances = []
        if self.ce_strike and self.spot:
            distances.append(self.ce_distance_pct)
        if self.pe_strike and self.spot:
            distances.append(self.pe_distance_pct)
        return min(distances) if distances else 999.0

    @property
    def status(self) -> str:
        d = self.worst_distance_pct
        if d < THRESHOLDS["CRITICAL"]: return "CRITICAL"
        if d < THRESHOLDS["WARNING"]:  return "WARNING"
        if d < THRESHOLDS["CAUTION"]:  return "CAUTION"
        return "SAFE"

    @property
    def status_icon(self) -> str:
        return {"SAFE": "🟢", "CAUTION": "🟡", "WARNING": "🟠", "CRITICAL": "🔴"}.get(self.status, "⚪")

    # ── Delta ────────────────────────────────────────────────────────

    def compute_deltas(self, greeks_map=None, as_of=None) -> None:
        """
        Attaches delta info to every leg from a pre-fetched greeks_map.
        greeks_map keys: (underlying, expiry, strike, option_type)
        greeks_map values: {"delta": float, "implied_vol_pct": float,
                            "converged": bool, "source": "dhan"|"bs"}
        If greeks_map is None or a key is missing, leg delta stays None.
        """
        if not greeks_map:
            return
        for leg in self.ce_legs + self.pe_legs:
            key = (self.underlying, self.expiry, leg.strike, leg.option_type)
            leg._delta_info = greeks_map.get(key, {
                "delta": None, "implied_vol_pct": None,
                "days_to_expiry": None, "converged": False,
            })

    @property
    def net_position_delta(self) -> Optional[float]:
        """
        Sum of (raw_delta × signed_quantity) across all legs. Kite's
        `quantity` is already in total contract units (lots × lot
        size), so this naturally accounts for position size — no
        separate lot-size multiplication is needed. Returns None if
        any leg's delta couldn't be computed (e.g. IV solver didn't
        converge for that leg).

        This is a raw, large-scale number. See lot_size and
        net_lot_equivalent_delta below for the more intuitive
        "equivalent lots of underlying" framing (matches how brokers
        like Sensibull display position delta).
        """
        total = 0.0
        for leg in self.ce_legs + self.pe_legs:
            d = leg.delta_info.get("delta")
            if d is None:
                return None
            total += d * leg.quantity
        return total

    @property
    def lot_size(self) -> Optional[int]:
        """Known NSE lot size for this underlying, or None if not in LOT_SIZE_MAP."""
        return LOT_SIZE_MAP.get(self.underlying)

    @property
    def net_lot_equivalent_delta(self) -> Optional[float]:
        """
        Net position delta expressed as "equivalent lots of the
        underlying" — net_position_delta ÷ lot_size. This is the
        number brokers like Sensibull display (e.g. 0.29 instead of a
        raw number like 57.5) — far more intuitive: it tells you
        directly how many lots of the underlying this position
        currently behaves like. Returns None if lot_size is unknown
        for this underlying, or if net_position_delta itself is None.
        """
        nd = self.net_position_delta
        if nd is None or self.lot_size is None:
            return None
        return nd / self.lot_size

    @property
    def delta_status(self) -> str:
        nd = self.net_position_delta
        if nd is None:
            return "UNKNOWN"
        lot = self.lot_size or DEFAULT_LOT_SIZE_FOR_NEUTRALITY
        threshold_neutral = NEUTRAL_LOT_MULTIPLE * lot
        threshold_slight  = SLIGHT_BIAS_LOT_MULTIPLE * lot
        if abs(nd) <= threshold_neutral:
            return "NEUTRAL"
        if abs(nd) <= threshold_slight:
            return "SLIGHT BIAS — " + ("LONG" if nd > 0 else "SHORT")
        return "SKEWED — " + ("LONG" if nd > 0 else "SHORT")

    @property
    def delta_status_icon(self) -> str:
        if self.delta_status == "NEUTRAL":
            return "🟢"
        if self.delta_status == "UNKNOWN":
            return "⚪"
        return "🟡" if "SLIGHT" in self.delta_status else "🔴"

    # ── Profit/Loss/Breakeven/POP — Sensibull-style position metrics ──

    @property
    def is_short_strangle(self) -> bool:
        """True if both sides are net short — the premium-selling case
        this dashboard is built around. Max profit / breakeven formulas
        below assume this; they return None otherwise."""
        ce_short = all(l.is_short for l in self.ce_legs) if self.ce_legs else True
        pe_short = all(l.is_short for l in self.pe_legs) if self.pe_legs else True
        return ce_short and pe_short

    def _weighted_avg_price(self, legs: list[ParsedOption]) -> float:
        total_qty = sum(l.abs_qty for l in legs)
        if total_qty == 0:
            return 0.0
        return sum(l.avg_price * l.abs_qty for l in legs) / total_qty

    @property
    def combined_entry_premium_per_share(self) -> float:
        """CE + PE average entry price per share (NOT × quantity) — the
        per-share number breakeven is computed from."""
        return self._weighted_avg_price(self.ce_legs) + self._weighted_avg_price(self.pe_legs)

    @property
    def lower_breakeven(self) -> Optional[float]:
        """At-expiry lower breakeven: PE strike − combined entry premium."""
        if not self.pe_strike:
            return None
        return self.pe_strike - self.combined_entry_premium_per_share

    @property
    def upper_breakeven(self) -> Optional[float]:
        """At-expiry upper breakeven: CE strike + combined entry premium."""
        if not self.ce_strike:
            return None
        return self.ce_strike + self.combined_entry_premium_per_share

    @property
    def lower_breakeven_pct(self) -> Optional[float]:
        be = self.lower_breakeven
        if be is None or not self.spot:
            return None
        return (be - self.spot) / self.spot * 100

    @property
    def upper_breakeven_pct(self) -> Optional[float]:
        be = self.upper_breakeven
        if be is None or not self.spot:
            return None
        return (be - self.spot) / self.spot * 100

    @property
    def max_profit(self) -> Optional[float]:
        """
        For a short strangle: max profit = total premium collected at
        entry (both legs expire worthless if spot stays between the
        strikes at expiry). Returns None if not a short strangle or
        the position isn't complete (missing a leg).
        """
        if not self.is_short_strangle or not self.is_complete:
            return None
        return sum(l.entry_value for l in self.ce_legs + self.pe_legs)

    def max_profit_pct(self, margin_used: Optional[float]) -> Optional[float]:
        """Max profit as % return on margin used. None if margin unknown."""
        mp = self.max_profit
        if mp is None or not margin_used:
            return None
        return mp / margin_used * 100

    @property
    def profit_left(self) -> Optional[float]:
        """Max profit minus current P&L — how much more is achievable."""
        mp = self.max_profit
        if mp is None:
            return None
        return mp - self.total_pnl

    def profit_left_pct(self, margin_used: Optional[float]) -> Optional[float]:
        pl = self.profit_left
        if pl is None or not margin_used:
            return None
        return pl / margin_used * 100

    @property
    def loss_left_label(self) -> str:
        """Short strangles carry theoretically unlimited risk on the call
        side (large but bounded-by-zero on the put side) — shown as
        'Unlimited', matching standard broker-app convention."""
        return "Unlimited" if self.is_short_strangle else "Defined"

    @property
    def average_iv_pct(self) -> Optional[float]:
        """Average of both legs' implied vol — used as the POP model's
        volatility input. None if either leg's IV is unavailable."""
        ivs = []
        for leg in self.ce_legs + self.pe_legs:
            iv = leg.delta_info.get("implied_vol_pct")
            if iv is None:
                return None
            ivs.append(iv)
        return sum(ivs) / len(ivs) if ivs else None

    @property
    def pop_pct(self) -> Optional[float]:
        """
        Probability of Profit — the risk-neutral probability (lognormal
        Black-Scholes distribution, using average IV across both legs)
        that the underlying finishes BETWEEN the two breakevens at
        expiry. Returns None if any required input is missing.
        """
        import math

        from dashboard.greeks import norm_cdf, parse_expiry_to_date

        lower, upper, iv_pct = self.lower_breakeven, self.upper_breakeven, self.average_iv_pct
        if lower is None or upper is None or iv_pct is None or not self.spot or lower <= 0:
            return None

        expiry_date = parse_expiry_to_date(self.expiry)
        if expiry_date is None:
            return None
        days = (expiry_date - date.today()).days
        T = max(days, 0) / 365.0
        if T <= 0:
            return None

        sigma = iv_pct / 100
        r = 0.065
        try:
            d2_upper = (math.log(upper / self.spot) - (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
            d2_lower = (math.log(lower / self.spot) - (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        except (ValueError, ZeroDivisionError):
            return None

        return (norm_cdf(d2_upper) - norm_cdf(d2_lower)) * 100


# ── Symbol parser ───────────────────────────────────────────────────────

# Kite/NSE uses TWO different tradingsymbol formats:
#   Index options (NIFTY, BANKNIFTY etc) — weekly + monthly:
#     SYMBOL + DDMMMYY + STRIKE + TYPE   e.g. NIFTY09JUL2648000CE
#   Equity stock options — monthly only:
#     SYMBOL + DDMMM   + STRIKE + TYPE   e.g. NTPC26JUN340CE
#                                             NTPC26JUN387.5CE  (decimal strike)
# The equity format omits the year entirely; the year is inferred from context.
# The critical consequence: without this distinction, the regex grabs the first
# 2 digits of the strike as the "year", making every leg appear to have a
# different expiry → they never group into a strangle.

def _normalize_expiry(ddmmm: str, as_of: date | None = None) -> str:
    """
    Convert 'DDMMM' (no year) → 'DDMMMYY' by inferring the nearest
    future monthly expiry year. Returns the original string unchanged
    if it already contains a year (len > 5).
    """
    ddmmm = ddmmm.strip().upper()
    if len(ddmmm) > 5:
        return ddmmm  # already has year component

    try:
        today = as_of or date.today()
        partial = datetime.strptime(ddmmm, "%d%b")
        # Try current year first; if the date is > 7 days in the past, use next year
        candidate = date(today.year, partial.month, partial.day)
        if candidate < today - timedelta(days=7):
            candidate = date(today.year + 1, partial.month, partial.day)
        return candidate.strftime("%d%b%y").upper()
    except ValueError:
        return ddmmm


def _expiry_is_plausible(expiry_str: str, as_of: date | None = None) -> bool:
    """
    Returns True if a DDMMMYY expiry string represents a date that could
    plausibly be an open option position from today's perspective.
    (Within 3 years of today in either direction.)
    This guard is what prevents the 2-digit year slot in Format 1 from
    greedily capturing the first 2 digits of the strike price instead.
    """
    try:
        today = as_of or date.today()
        d = datetime.strptime(expiry_str.upper(), "%d%b%y").date()
        delta_days = (d - today).days
        return -7 < delta_days < 365 * 3   # within 7 days past to ~3 years future
    except ValueError:
        return False


def parse_option_symbol(tradingsymbol: str, as_of: date | None = None) -> Optional[ParsedOption]:
    ts = tradingsymbol.strip().upper()

    for name in KNOWN_UNDERLYINGS:
        if not ts.startswith(name):
            continue
        suffix = ts[len(name):]

        # Format 1: DDMMMYY + STRIKE  (index options with year)
        # Only accepted if the parsed expiry date is plausible — otherwise
        # the regex has falsely grabbed the first 2 strike digits as the year.
        m = re.match(r"^(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$", suffix)
        if m and _expiry_is_plausible(m.group(1), as_of):
            return ParsedOption(
                tradingsymbol=tradingsymbol, underlying=name,
                expiry=m.group(1),
                strike=float(m.group(2)), option_type=m.group(3),
                quantity=0, avg_price=0.0, ltp=0.0, pnl=0.0,
            )

        # Format 2: DDMMM + STRIKE  (equity monthly options, no year in symbol)
        m = re.match(r"^(\d{2}[A-Z]{3})(\d+(?:\.\d+)?)(CE|PE)$", suffix)
        if m:
            return ParsedOption(
                tradingsymbol=tradingsymbol, underlying=name,
                expiry=_normalize_expiry(m.group(1), as_of),
                strike=float(m.group(2)), option_type=m.group(3),
                quantity=0, avg_price=0.0, ltp=0.0, pnl=0.0,
            )

    # Fallback: underlying not in known list — try both formats
    m = re.match(r"^([A-Z]+[&-]?[A-Z]*)(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$", ts)
    if m and _expiry_is_plausible(m.group(2), as_of):
        return ParsedOption(
            tradingsymbol=tradingsymbol, underlying=m.group(1), expiry=m.group(2),
            strike=float(m.group(3)), option_type=m.group(4),
            quantity=0, avg_price=0.0, ltp=0.0, pnl=0.0,
        )

    m = re.match(r"^([A-Z]+[&-]?[A-Z]*)(\d{2}[A-Z]{3})(\d+(?:\.\d+)?)(CE|PE)$", ts)
    if m:
        return ParsedOption(
            tradingsymbol=tradingsymbol, underlying=m.group(1),
            expiry=_normalize_expiry(m.group(2), as_of),
            strike=float(m.group(3)), option_type=m.group(4),
            quantity=0, avg_price=0.0, ltp=0.0, pnl=0.0,
        )

    return None


def group_positions_into_strangles(
    raw_positions: list[dict], spot_prices: dict[str, float],
) -> tuple[list[Strangle], list[dict]]:
    """
    Groups by (connection_id, underlying, expiry). Positions from
    different broker connections are NEVER merged, even if they share
    the same underlying+expiry — they're genuinely separate positions
    in separate accounts.
    """
    groups: dict[tuple, dict] = defaultdict(lambda: {"ce": [], "pe": []})
    unmatched: list[dict] = []

    for raw in raw_positions:
        if raw.get("quantity", 0) == 0:
            continue
        parsed = parse_option_symbol(raw.get("tradingsymbol", ""))
        if parsed is None:
            unmatched.append(raw)
            continue

        parsed.quantity         = raw.get("quantity", 0)
        parsed.avg_price        = raw.get("average_price", 0.0)
        parsed.ltp              = raw.get("last_price", raw.get("ltp", 0.0))
        parsed.pnl              = raw.get("pnl", 0.0)
        parsed.connection_id    = raw.get("_connection_id", "")
        parsed.connection_label = raw.get("_connection_label", "")

        key = (parsed.connection_id, parsed.underlying, parsed.expiry)
        groups[key]["ce" if parsed.option_type == "CE" else "pe"].append(parsed)

    strangles: list[Strangle] = []
    for (connection_id, underlying, expiry), legs in groups.items():
        label = ""
        if legs["ce"]:
            label = legs["ce"][0].connection_label
        elif legs["pe"]:
            label = legs["pe"][0].connection_label

        s = Strangle(
            underlying=underlying, expiry=expiry,
            connection_id=connection_id, connection_label=label,
            ce_legs=legs["ce"], pe_legs=legs["pe"],
            spot=spot_prices.get(underlying, 0.0),
        )
        strangles.append(s)

    strangles.sort(key=lambda s: (s.connection_label, s.underlying))
    return strangles, unmatched


# ── Mock sample positions (for connections with no real Kite token) ────
# Expiry is generated dynamically (next month-end-ish, ~3 weeks out) so
# delta calculations on mock data always have a sensible time-to-expiry,
# rather than a hardcoded date that silently drifts into the past.

def _mock_expiry_str(days_out: int = 20) -> str:
    from datetime import timedelta
    return (date.today() + timedelta(days=days_out)).strftime("%d%b%y").upper()


_MOCK_EXPIRY = _mock_expiry_str()

MOCK_POSITIONS: list[dict] = [
    {"tradingsymbol": f"BANKNIFTY{_MOCK_EXPIRY}54000CE", "quantity": -30,
     "average_price": 125.0, "last_price": 85.0, "pnl": 1200.0},
    {"tradingsymbol": f"BANKNIFTY{_MOCK_EXPIRY}48000PE", "quantity": -30,
     "average_price": 80.0, "last_price": 45.0, "pnl": 1050.0},
    {"tradingsymbol": f"NIFTY{_MOCK_EXPIRY}27000CE", "quantity": -50,
     "average_price": 55.0, "last_price": 35.0, "pnl": 1000.0},
    {"tradingsymbol": f"NIFTY{_MOCK_EXPIRY}22500PE", "quantity": -50,
     "average_price": 40.0, "last_price": 120.0, "pnl": -4000.0},
]
