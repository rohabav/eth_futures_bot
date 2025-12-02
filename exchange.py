# exchange.py
import time
import hmac
import hashlib
import urllib.parse
from typing import Dict, Any, Optional, List

import requests

from config import (
    BINANCE_FAPI_BASE,
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    SYMBOL,
    TARGET_LEVERAGE,
    MARGIN_TYPE,
)


class BinanceFuturesClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    # ---------- Internal helpers ----------

    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, params: Dict[str, Any]) -> str:
        query_string = urllib.parse.urlencode(params, doseq=True)
        return hmac.new(self.api_secret, query_string.encode(), hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        signed: bool = False,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if params is None:
            params = {}

        if signed:
            params["timestamp"] = self._timestamp()
            params["recvWindow"] = 5000
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{path}"

        if method == "GET":
            resp = self.session.get(url, params=params, timeout=10)
        elif method == "POST":
            resp = self.session.post(url, params=params, timeout=10)
        elif method == "DELETE":
            resp = self.session.delete(url, params=params, timeout=10)
        else:
            raise ValueError(f"Unsupported method {method}")

        if resp.status_code != 200:
            raise Exception(f"Binance API error {resp.status_code}: {resp.text}")

        return resp.json()

    # ---------- Public (no auth) endpoints ----------

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> List[List[Any]]:
        """
        Kline/candlestick data.
        """
        return self._request(
            "GET",
            "/fapi/v1/klines",
            signed=False,
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )

    def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Order book depth (bids/asks).
        """
        return self._request(
            "GET",
            "/fapi/v1/depth",
            signed=False,
            params={"symbol": symbol, "limit": limit},
        )

    def get_recent_trades(self, symbol: str, limit: int = 50) -> Any:
        """
        Recent public trades.
        """
        return self._request(
            "GET",
            "/fapi/v1/trades",
            signed=False,
            params={"symbol": symbol, "limit": limit},
        )

    def get_funding_rate(self, symbol: str, limit: int = 1) -> Any:
        """
        Funding rate history.
        """
        return self._request(
            "GET",
            "/fapi/v1/fundingRate",
            signed=False,
            params={"symbol": symbol, "symbol": symbol, "limit": limit},
        )

    # ---------- Private (signed) endpoints ----------

    def get_account(self) -> Dict[str, Any]:
        """
        Futures account info (balances, positions summary, etc.).
        """
        return self._request("GET", "/fapi/v2/account", signed=True)

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Detailed position info (per symbol).
        """
        return self._request("GET", "/fapi/v2/positionRisk", signed=True)

    def change_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> None:
        """
        Set margin type (ISOLATED or CROSSED).
        If it's already the requested type, Binance returns error -4046,
        which we treat as a harmless info.
        """
        try:
            self._request(
                "POST",
                "/fapi/v1/marginType",
                signed=True,
                params={"symbol": symbol, "marginType": margin_type},
            )
            print(f"[INFO] Margin type set to {margin_type} for {symbol}.")
        except Exception as e:
            msg = str(e)
            if "No need to change margin type" in msg or '"code":-4046' in msg:
                # Already using the requested margin type
                print(f"[INFO] Margin type already {margin_type} for {symbol}, no change needed.")
            else:
                # Some other error worth attention
                print(f"[WARN] change_margin_type unexpected error: {e}")

    def change_leverage(self, symbol: str, leverage: int) -> None:
        """
        Set leverage for a symbol.
        """
        try:
            self._request(
                "POST",
                "/fapi/v1/leverage",
                signed=True,
                params={"symbol": symbol, "leverage": leverage},
            )
            print(f"[INFO] Leverage set to {leverage}x for {symbol}.")
        except Exception as e:
            print(f"[WARN] change_leverage error: {e}")

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Place a MARKET order (BUY for long, SELL for short).
        """
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,  # "BUY" or "SELL"
            "type": "MARKET",
            "quantity": quantity,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        return self._request("POST", "/fapi/v1/order", signed=True, params=params)

    def cancel_all_orders(self, symbol: str) -> Any:
        """
        Cancel all open orders for the symbol.
        (Not strictly needed in current logic but handy to have.)
        """
        return self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            signed=True,
            params={"symbol": symbol},
        )


def init_client() -> BinanceFuturesClient:
    """
    Initialize the client, ensure margin type & leverage,
    and return a ready-to-use BinanceFuturesClient instance.
    """
    client = BinanceFuturesClient(
        BINANCE_FAPI_BASE,
        BINANCE_API_KEY,
        BINANCE_API_SECRET,
    )

    # Make sure margin type and leverage match our config
    client.change_margin_type(SYMBOL, MARGIN_TYPE)
    client.change_leverage(SYMBOL, TARGET_LEVERAGE)

    return client
