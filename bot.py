# bot.py
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from config import (
    SYMBOL,
    CHECK_INTERVAL_SECONDS,
    DEFAULT_STOP_LOSS_PCT,
    TF_MAIN,
    TF_MID,
    TF_HIGH,
)
from exchange import init_client
from risk import init_risk_state, maybe_reset_day, can_open_new_trade, compute_position_size
from strategy import evaluate_strategy
from indicators import ema, rsi, macd
from telegram_bot import send_telegram_message


def get_mark_price(client) -> float:
    """
    Use ticker price as a proxy for mark price.
    """
    data = client._request("GET", "/fapi/v1/ticker/price", signed=False, params={"symbol": SYMBOL})
    return float(data["price"])


def get_wallet_equity_and_balance(client):
    """
    Reads futures account info and extracts:
      - totalWalletBalance as 'equity'
      - USDT walletBalance as 'wallet_balance'

    Logs only a compact summary:
      - equity
      - USDT asset
      - ETHUSDT position (if any)
    """
    acct = client.get_account()

    # Total wallet equity (all assets converted to USDT)
    equity = float(acct.get("totalWalletBalance", 0.0))

    # Find USDT asset only
    wallet_balance = 0.0
    usdt_asset = None
    for a in acct.get("assets", []):
        if a.get("asset") == "USDT":
            wallet_balance = float(a.get("walletBalance", 0.0))
            usdt_asset = a
            break

    # Optional: find ETHUSDT position only
    eth_pos = None
    for p in acct.get("positions", []):
        if p.get("symbol") == "ETHUSDT":
            eth_pos = p
            break

    # Nice, compact debug log
    print("[DEBUG] Account summary:")
    print(f"  equity (totalWalletBalance) = {equity}")
    if usdt_asset:
        print(f"  USDT walletBalance         = {wallet_balance}")
    if eth_pos:
        print(
            "  ETHUSDT position: "
            f"amt={eth_pos.get('positionAmt')}, "
            f"entry={eth_pos.get('entryPrice')}, "
            f"unrealized={eth_pos.get('unrealizedProfit')}"
        )

    return equity, wallet_balance


def get_open_position_info(client):
    """
    Returns info about open ETHUSDT position, or None if flat.
    """
    positions = client.get_positions()
    for p in positions:
        if p["symbol"] == SYMBOL and float(p["positionAmt"]) != 0:
            qty = float(p["positionAmt"])
            entry_price = float(p["entryPrice"])
            side = "BUY" if qty > 0 else "SELL"
            return {
                "qty": abs(qty),
                "entry_price": entry_price,
                "side": side,
            }
    return None


def compute_stop_price(entry_price: float, side: str) -> float:
    """
    Compute fixed-percentage stop price from entry.
    """
    if side == "BUY":
        return entry_price * (1 - DEFAULT_STOP_LOSS_PCT)
    else:
        return entry_price * (1 + DEFAULT_STOP_LOSS_PCT)


def should_exit_by_indicators(client, tf: str = "5m") -> bool:
    """
    Quick indicator-based exit check using latest 5m candles:
      - MACD histogram cross through zero (trend shift).
    """
    klines = client.get_klines(SYMBOL, tf, limit=60)
    df = pd.DataFrame(
        klines,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore",
        ],
    ).iloc[:-1]  # drop open candle
    df["close"] = df["close"].astype(float)

    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], 14)
    macd_line, signal_line, hist = macd(df["close"])
    df["macd_hist"] = hist

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Risk-averse exit conditions, we interpret symmetrical for long/short in main loop
    return (prev["macd_hist"] > 0 >= last["macd_hist"]) or (prev["macd_hist"] < 0 <= last["macd_hist"])


def main():
    print("[INFO] Initializing Binance client...")
    client = init_client()
    print("[INFO] Client initialized, fetching initial equity/balance...")

    equity, wallet_balance = get_wallet_equity_and_balance(client)
    print(f"[INFO] Initial equity={equity}, wallet_balance={wallet_balance}")

    risk_state = init_risk_state(equity)

    send_telegram_message(f"ETH Futures bot started on {datetime.now(timezone.utc)} (env active).")
    print("[INFO] Entering main loop...")

    while True:
        loop_start = time.time()
        try:
            # --- Update account & risk ---
            equity, wallet_balance = get_wallet_equity_and_balance(client)
            risk_state = maybe_reset_day(risk_state, equity)
            allowed = can_open_new_trade(risk_state, equity)

            pos_info: Optional[dict] = get_open_position_info(client)
            mark_price = get_mark_price(client)

            # --- Manage open position (check exits) ---
            if pos_info:
                side = pos_info["side"]
                entry_price = pos_info["entry_price"]
                qty = pos_info["qty"]

                stop_price = compute_stop_price(entry_price, side)

                # Hard stop-loss
                if side == "BUY" and mark_price <= stop_price:
                    client.create_market_order(SYMBOL, "SELL", qty, reduce_only=True)
                    send_telegram_message(
                        f"Stop-loss hit on long {SYMBOL}: entry={entry_price}, stop={stop_price}, mark={mark_price}"
                    )
                    print(f"[INFO] SL hit on long {SYMBOL} at {mark_price}")
                elif side == "SELL" and mark_price >= stop_price:
                    client.create_market_order(SYMBOL, "BUY", qty, reduce_only=True)
                    send_telegram_message(
                        f"Stop-loss hit on short {SYMBOL}: entry={entry_price}, stop={stop_price}, mark={mark_price}"
                    )
                    print(f"[INFO] SL hit on short {SYMBOL} at {mark_price}")
                else:
                    # Indicator-based exit (trend shift)
                    if should_exit_by_indicators(client):
                        exit_side = "SELL" if side == "BUY" else "BUY"
                        client.create_market_order(SYMBOL, exit_side, qty, reduce_only=True)
                        send_telegram_message(f"Indicator exit on {side} {SYMBOL} at mark {mark_price}")
                        print(f"[INFO] Indicator-based exit on {side} {SYMBOL} at {mark_price}")

            else:
                # --- No open position: maybe open a new one ---
                if allowed:
                    kl_5 = client.get_klines(SYMBOL, TF_MAIN, limit=200)
                    kl_15 = client.get_klines(SYMBOL, TF_MID, limit=200)
                    kl_1h = client.get_klines(SYMBOL, TF_HIGH, limit=200)
                    order_book = client.get_order_book(SYMBOL, limit=20)

                    signal = evaluate_strategy(kl_5, kl_15, kl_1h, order_book)
                    if signal:
                        qty = compute_position_size(wallet_balance, mark_price)
                        if qty > 0:
                            resp = client.create_market_order(SYMBOL, signal.side, qty)
                            send_telegram_message(
                                f"Opened {signal.side} {SYMBOL}, qty={qty}, price≈{mark_price}, reason={signal.reason}"
                            )
                            print(
                                f"[INFO] Opened {signal.side} {SYMBOL}, qty={qty}, price≈{mark_price}, "
                                f"reason={signal.reason}"
                            )
                    else:
                        # No trade this loop, log a compact heartbeat
                        print(
                            f"[INFO] No signal this loop. "
                            f"equity={equity}, wallet={wallet_balance}, price={mark_price}"
                        )
                else:
                    print("[INFO] Daily drawdown limit hit, not opening new positions today.")

        except Exception as e:
            import traceback
            print("[ERROR] Exception inside main loop:")
            traceback.print_exc()
            send_telegram_message(f"[ERROR] {e}")

        # Sleep to roughly hit every 5 minutes
        elapsed = time.time() - loop_start
        sleep_for = max(5, CHECK_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    # Top-level guard so startup errors are visible in Railway logs
    try:
        main()
    except Exception as e:
        import traceback
        print("[FATAL] Unhandled exception in bot startup:")
        traceback.print_exc()
        try:
            send_telegram_message(f"[FATAL] Bot crashed on startup: {e}")
        except Exception:
            pass
