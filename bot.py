# bot.py
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from config import (
    SYMBOL,
    CHECK_INTERVAL_SECONDS,
    TF_MAIN,
    TF_MID,
    TF_HIGH,
    ATR_PERIOD,
    ATR_SL_MULTIPLIER,
    ATR_TP_MULTIPLIER,
)
from exchange import init_client
from risk import init_risk_state, maybe_reset_day, can_open_new_trade, compute_position_size
from strategy import evaluate_strategy
from indicators import atr
from telegram_bot import send_telegram_message


def get_mark_price(client) -> float:
    """Use ticker price as a proxy for mark price."""
    data = client._request("GET", "/fapi/v1/ticker/price", signed=False, params={"symbol": SYMBOL})
    return float(data["price"])


def get_wallet_equity_and_balance(client):
    """
    Reads futures account info and extracts:
      - totalWalletBalance as 'equity'
      - USDT walletBalance as 'wallet_balance'

    Logs a compact summary:
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
        if p.get("symbol") == SYMBOL:
            eth_pos = p
            break

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


def _klines_to_df(klines) -> pd.DataFrame:
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(klines, columns=cols)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def compute_atr_from_15m(klines_15m) -> Optional[float]:
    """
    Compute last ATR value from 15m klines.
    """
    df15 = _klines_to_df(klines_15m)
    if len(df15) < ATR_PERIOD + 2:
        return None
    atr_series = atr(df15)
    return float(atr_series.iloc[-1])


def compute_pnl(entry_price: float, exit_price: float, qty: float, side: str):
    """
    Approximate realized PnL in USDT and % on the position notional.
    For USDT-margined futures, PnL = (exit - entry) * qty for long,
    and (entry - exit) * qty for short.
    """
    if qty <= 0 or entry_price <= 0:
        return 0.0, 0.0

    if side == "BUY":
        pnl = (exit_price - entry_price) * qty
    else:
        pnl = (entry_price - exit_price) * qty

    notional = entry_price * qty
    pnl_pct = (pnl / notional) * 100 if notional > 0 else 0.0
    return pnl, pnl_pct


def main():
    print("[INFO] Initializing Binance client...")
    client = init_client()
    print("[INFO] Client initialized, fetching initial equity/balance...")

    equity, wallet_balance = get_wallet_equity_and_balance(client)
    print(f"[INFO] Initial equity={equity}, wallet_balance={wallet_balance}")

    risk_state = init_risk_state(equity)

    send_telegram_message(f"🚀 ETH Futures bot started on {datetime.now(timezone.utc)} (env active).")
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

            # --- Fetch candles for all timeframes (used for both entry & ATR exits) ---
            kl_15 = client.get_klines(SYMBOL, TF_MAIN, limit=200)   # 15m
            kl_30 = client.get_klines(SYMBOL, TF_MID, limit=200)    # 30m
            kl_1h = client.get_klines(SYMBOL, TF_HIGH, limit=200)   # 1h

            atr_15 = compute_atr_from_15m(kl_15)

            # --- Position management: ATR-based SL & TP ---
            if pos_info and atr_15 is not None:
                side = pos_info["side"]
                entry_price = pos_info["entry_price"]
                qty = pos_info["qty"]

                if side == "BUY":
                    sl = entry_price - ATR_SL_MULTIPLIER * atr_15
                    tp = entry_price + ATR_TP_MULTIPLIER * atr_15
                else:  # SHORT
                    sl = entry_price + ATR_SL_MULTIPLIER * atr_15
                    tp = entry_price - ATR_TP_MULTIPLIER * atr_15

                decision_expl = (
                    f"In position ({side}) – ATR15={atr_15:.2f}, entry={entry_price:.2f}, "
                    f"SL={sl:.2f}, TP={tp:.2f}, mark={mark_price:.2f}."
                )

                exited = False

                # SL / TP logic
                if side == "BUY" and mark_price <= sl:
                    client.create_market_order(SYMBOL, "SELL", qty, reduce_only=True)
                    pnl, pnl_pct = compute_pnl(entry_price, mark_price, qty, side)
                    msg = (
                        f"❌ SL hit on LONG {SYMBOL}\n"
                        f"Qty: {qty}\n"
                        f"Entry: {entry_price:.2f}\n"
                        f"Exit:  {mark_price:.2f}\n"
                        f"ATR15: {atr_15:.2f}\n"
                        f"PnL:   {pnl:.2f} USDT ({pnl_pct:.2f}%)"
                    )
                    send_telegram_message(msg)
                    print(f"[INFO] SL hit on LONG {SYMBOL} at {mark_price}, pnl={pnl:.2f} USDT")
                    decision_expl += " Decision: EXIT via SL (long)."
                    exited = True

                elif side == "BUY" and mark_price >= tp:
                    client.create_market_order(SYMBOL, "SELL", qty, reduce_only=True)
                    pnl, pnl_pct = compute_pnl(entry_price, mark_price, qty, side)
                    msg = (
                        f"✅ TP hit on LONG {SYMBOL}\n"
                        f"Qty: {qty}\n"
                        f"Entry: {entry_price:.2f}\n"
                        f"Exit:  {mark_price:.2f}\n"
                        f"ATR15: {atr_15:.2f}\n"
                        f"PnL:   {pnl:.2f} USDT ({pnl_pct:.2f}%)"
                    )
                    send_telegram_message(msg)
                    print(f"[INFO] TP hit on LONG {SYMBOL} at {mark_price}, pnl={pnl:.2f} USDT")
                    decision_expl += " Decision: EXIT via TP (long)."
                    exited = True

                elif side == "SELL" and mark_price >= sl:
                    client.create_market_order(SYMBOL, "BUY", qty, reduce_only=True)
                    pnl, pnl_pct = compute_pnl(entry_price, mark_price, qty, side)
                    msg = (
                        f"❌ SL hit on SHORT {SYMBOL}\n"
                        f"Qty: {qty}\n"
                        f"Entry: {entry_price:.2f}\n"
                        f"Exit:  {mark_price:.2f}\n"
                        f"ATR15: {atr_15:.2f}\n"
                        f"PnL:   {pnl:.2f} USDT ({pnl_pct:.2f}%)"
                    )
                    send_telegram_message(msg)
                    print(f"[INFO] SL hit on SHORT {SYMBOL} at {mark_price}, pnl={pnl:.2f} USDT")
                    decision_expl += " Decision: EXIT via SL (short)."
                    exited = True

                elif side == "SELL" and mark_price <= tp:
                    client.create_market_order(SYMBOL, "BUY", qty, reduce_only=True)
                    pnl, pnl_pct = compute_pnl(entry_price, mark_price, qty, side)
                    msg = (
                        f"✅ TP hit on SHORT {SYMBOL}\n"
                        f"Qty: {qty}\n"
                        f"Entry: {entry_price:.2f}\n"
                        f"Exit:  {mark_price:.2f}\n"
                        f"ATR15: {atr_15:.2f}\n"
                        f"PnL:   {pnl:.2f} USDT ({pnl_pct:.2f}%)"
                    )
                    send_telegram_message(msg)
                    print(f"[INFO] TP hit on SHORT {SYMBOL} at {mark_price}, pnl={pnl:.2f} USDT")
                    decision_expl += " Decision: EXIT via TP (short)."
                    exited = True

                if not exited:
                    decision_expl += " Decision: HOLD – price between SL and TP."

                # Every 5m: explanation to logs & Telegram
                print(f"[INFO] Decision loop (in position): {decision_expl}")
                send_telegram_message(f"📊 Decision loop:\n{decision_expl}")

            else:
                # --- Flat: evaluate new entry ---
                signal, expl = evaluate_strategy(kl_15, kl_30, kl_1h)
                # Explain decision every loop
                print(f"[INFO] Decision loop (flat):\n{expl}")
                send_telegram_message(f"📊 Decision loop:\n{expl}")

                if signal and allowed:
                    qty = compute_position_size(wallet_balance, mark_price)
                    if qty > 0:
                        client.create_market_order(SYMBOL, signal.side, qty)
                        # Give exchange a tiny moment to register the position, then read entry
                        time.sleep(1)
                        new_pos = get_open_position_info(client)
                        if new_pos:
                            entry_price = new_pos["entry_price"]
                        else:
                            entry_price = mark_price

                        msg = (
                            f"✅ Opened {signal.side} {SYMBOL}\n"
                            f"Qty:   {qty}\n"
                            f"Entry: {entry_price:.2f}\n"
                            f"Reason: {signal.reason}"
                        )
                        send_telegram_message(msg)
                        print(
                            f"[INFO] Opened {signal.side} {SYMBOL}, "
                            f"qty={qty}, entry≈{entry_price}, reason={signal.reason}"
                        )
                elif signal and not allowed:
                    # You have a signal but risk rules say no new trades today
                    print("[INFO] Signal present but daily drawdown limit hit, not opening new position.")
                    send_telegram_message(
                        "⚠️ Signal detected but daily drawdown limit hit. No new positions opened today."
                    )

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
