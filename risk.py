# risk.py
from dataclasses import dataclass
from datetime import datetime, timezone

from config import TRADE_BALANCE_FRACTION, DAILY_DRAWDOWN_LIMIT


@dataclass
class RiskState:
    day_start_equity: float
    day_start_date: str  # "YYYY-MM-DD"


def get_utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_risk_state(current_equity: float) -> RiskState:
    return RiskState(day_start_equity=current_equity, day_start_date=get_utc_date_str())


def maybe_reset_day(state: RiskState, current_equity: float) -> RiskState:
    today = get_utc_date_str()
    if today != state.day_start_date:
        return RiskState(day_start_equity=current_equity, day_start_date=today)
    return state


def can_open_new_trade(state: RiskState, current_equity: float) -> bool:
    loss_pct = (state.day_start_equity - current_equity) / state.day_start_equity
    return loss_pct < DAILY_DRAWDOWN_LIMIT


def compute_position_size(wallet_balance: float, mark_price: float) -> float:
    """
    Returns quantity in ETH using 10% of wallet balance.
    """
    usd_to_use = wallet_balance * TRADE_BALANCE_FRACTION
    if usd_to_use <= 0 or mark_price <= 0:
        return 0.0
    qty = usd_to_use / mark_price
    # Round down a bit to be safe; Binance ETH step size is usually 0.001
    return float(f"{qty:.3f}")
