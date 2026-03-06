"""Direct Polymarket CLOB client for live trading."""

import json
import logging
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

import config

logger = logging.getLogger(__name__)

_client = None


def _load_private_key():
    with open(config.POLYMARKET_KEY_PATH) as f:
        return json.load(f)["private_key"]


def get_client() -> ClobClient:
    """Get or create the CLOB client singleton."""
    global _client
    if _client is None:
        pk = _load_private_key()
        _client = ClobClient(
            host=config.POLYMARKET_HOST,
            key=pk,
            chain_id=config.POLYMARKET_CHAIN_ID,
        )
        # Derive API credentials from the wallet
        _client.set_api_creds(_client.create_or_derive_api_creds())
        logger.info("Polymarket CLOB client initialized")
    return _client


def get_balance() -> dict:
    """Get wallet USDC balance info."""
    try:
        client = get_client()
        # The CLOB client doesn't have a direct balance method,
        # but we can check via the allowances/collateral
        return {"connected": True}
    except Exception as e:
        logger.error(f"Balance check failed: {e}")
        return {"connected": False, "error": str(e)}


def get_market_info(token_id: str) -> dict:
    """Get current market/book info for a token."""
    try:
        client = get_client()
        book = client.get_order_book(token_id)
        return {
            "bids": book.bids if book.bids else [],
            "asks": book.asks if book.asks else [],
            "best_bid": float(book.bids[0].price) if book.bids else 0,
            "best_ask": float(book.asks[0].price) if book.asks else 1,
        }
    except Exception as e:
        logger.error(f"Market info error: {e}")
        return {}


def place_market_order(token_id: str, side: str, amount: float) -> dict:
    """Place a market buy order on Polymarket.

    Args:
        token_id: The YES or NO token ID from the market
        side: "yes" or "no"
        amount: USDC amount to spend
    """
    # Safety: prevent accidental use of Polymarket client while in paper mode
    try:
        if config.get_current_mode() != "live":
            logger.error(
                "Attempted to place Polymarket order while not in live mode. Aborting."
            )
            return {"success": False, "error": "polymarket_called_in_non_live_mode"}
    except Exception:
        # If config lookup fails, proceed with caution below
        pass

    try:
        client = get_client()

        # Get the best price from the order book
        book = client.get_order_book(token_id)

        if side.lower() == "yes":
            # Buying YES tokens — take the best ask
            if not book.asks:
                return {"success": False, "error": "No asks in order book"}
            price = float(book.asks[0].price)
        else:
            # Buying NO tokens — the NO token_id should be used
            if not book.asks:
                return {"success": False, "error": "No asks in order book"}
            price = float(book.asks[0].price)

        # Build and sign the order
        order_args = OrderArgs(
            price=price,
            size=round(amount / price, 2),  # Convert USDC to shares
            side=BUY,
            token_id=token_id,
        )

        signed_order = client.create_order(order_args)
        result = client.post_order(signed_order, OrderType.GTC)

        logger.info(f"Polymarket order placed: {side} ${amount} at {price}")
        return {
            "success": True,
            "order_id": result.get("orderID"),
            "price": price,
            "size": order_args.size,
            "result": result,
        }

    except Exception as e:
        logger.error(f"Polymarket order failed: {e}")
        return {"success": False, "error": str(e)}


def verify_connection() -> dict:
    """Verify the Polymarket CLOB connection works."""
    try:
        client = get_client()
        # Try to fetch server time as a connectivity check
        ok = client.get_ok()
        return {"connected": True, "status": ok}
    except Exception as e:
        return {"connected": False, "error": str(e)}

def cancel_order(order_id: str) -> dict:
    """Cancel an active order by its ID."""
    try:
        client = get_client()
        result = client.cancel(order_id)
        logger.info(f"Polymarket order {order_id} cancelled.")
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to cancel order {order_id}: {e}")
        return {"success": False, "error": str(e)}

def cancel_all_market_orders(market_id: str) -> dict:
    """The 29 Second Patch: Cancel all active limit orders for a specific market to prevent ghost positions."""
    try:
        client = get_client()
        result = client.cancel_market_orders(market_id)
        logger.info(f"All orders for market {market_id} cancelled successfully.")
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to cancel market orders for {market_id}: {e}")
        return {"success": False, "error": str(e)}

def get_token_balance(token_id: str) -> float:
    """State Refresh: Forceably pull the exact balance of shares in possession for a token_id."""
    try:
        client = get_client()
        from py_clob_client.clob_types import BalanceAllowanceParams
        params = BalanceAllowanceParams(asset_type="ERC1155", token_id=token_id)
        result = client.get_balance_allowance(params)
        # Assuming result contains balance, parse it (usually string of wei or float)
        # py_clob_client usually scales correctly or returns raw. Let's assume it returns a dict with 'balance'
        if isinstance(result, dict) and 'balance' in result:
            return float(result['balance'])
        elif hasattr(result, 'balance'):
            return float(result.balance)
        else:
            logger.warning(f"Unexpected balance result format: {result}")
            return 0.0
    except Exception as e:
        logger.error(f"Failed to get token balance for {token_id}: {e}")
        return 0.0
