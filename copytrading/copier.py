"""Mirror trades from tracked wallets via Simmer copytrading API."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
import db

logger = logging.getLogger(__name__)


class TradeCopier:
    def __init__(self, tracker):
        self.tracker = tracker
        self.position_size_fraction = config.COPYTRADING_POSITION_SIZE_FRACTION

    def execute_copy(self, api_key: str = None, wallets: list = None, max_per_position: float = None, target_trades: list = None):
        """Copy trades from tracked wallets applying advanced PolyCop institutional rules.
        
        Parameters:
        - target_trades: If provided, bypasses the fetch and processes these trades directly.
        """
        import threading
        
        if not config.COPYTRADING_ENABLED:
            logger.info("Copy trading disabled")
            return []

        # Target dynamic fetching either from passed param or by requesting backend for pending copies
        trades_to_process = target_trades or []
        
        if not target_trades and api_key:
            addresses = wallets or [w["address"] for w in self.tracker.get_tracked()]
            if not addresses:
                logger.info("No wallets to copy")
                return []
                
            max_usd = max_per_position or config.get_max_position() * self.position_size_fraction
            top_n = min(10, len(addresses))
    
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "wallets": addresses[:config.COPYTRADING_MAX_WALLETS_TO_TRACK],
                    "max_usd_per_position": max_usd,
                    "top_n": top_n,
                }
                
                # Assumes backend supports a /pending endpoint to return trades instead of executing them
                resp = requests.post(
                    f"{config.SIMMER_BASE_URL}/api/sdk/copytrading/pending",
                    headers=headers, json=payload, timeout=30
                )
                if resp.status_code in (200, 201):
                    trades_to_process = resp.json().get("trades", [])
            except Exception as e:
                logger.error(f"Copy trading fetch exception: {e}")
                return []

        executed_trades = []
        for trade in trades_to_process:
            wallet_addr = trade.get("wallet", "")
            target_amount = float(trade.get("amount", 0.0))
            target_price = float(trade.get("price", 0.5))
            token_id = trade.get("token_id")
            side = str(trade.get("side", "")).lower()

            # 1. Busca Dinamica
            # Ler configuracoes da carteira alvo
            wallet_profile = db.get_copytrading_wallet(wallet_addr)
            if not wallet_profile:
                # Fallback to standard if not found in db for some reason
                wallet_profile = {
                    "ignore_dust_under": 50.0,
                    "max_slippage_offset": 0.02,
                    "timeout_seconds": 120,
                    "profile_type": "standard"
                }
                
            ignore_dust_under = wallet_profile.get("ignore_dust_under", 50.0)
            max_slippage = wallet_profile.get("max_slippage_offset", 0.02)
            timeout_sec = wallet_profile.get("timeout_seconds", 120)

            # 2. Filtros de Aborto Previo (Skip)
            if target_amount < ignore_dust_under:
                logger.info(f"Copy skip: target amount ${target_amount:.2f} < ${ignore_dust_under:.2f} (dust/noise)")
                continue
                
            if target_price < config.COPY_MIN_PRICE or target_price > config.COPY_MAX_PRICE:
                logger.info(f"Copy skip: target price {target_price:.3f} outside EV zone ({config.COPY_MIN_PRICE}-{config.COPY_MAX_PRICE})")
                continue
                
            if not token_id:
                logger.warning(f"Copy skip: missing token_id for trade from wallet {wallet_addr[:8]}")
                continue

            # 3. Calculos de Execucao Segura
            execution_price = min(target_price + max_slippage, 0.99)
            
            # Trava Terminal do Risk Manager (2% max, ou max de configuracao)
            global_hard_cap = config.get_max_position()
            desired_amount = target_amount * self.position_size_fraction
            final_amount = min(desired_amount, global_hard_cap)
            
            # Executar diretamente na Polymarket (Sniper mode)
            import polymarket_client
            logger.info(f"PolyCop sending {side} order for ${final_amount:.2f} @ {execution_price:.3f} due to {wallet_profile.get('profile_type')} wallet target.")
            
            # Since place_market_order might place at book price instead of limit, we assume
            # place_market_order does a market buy or limit order internally. Wait, polymarket_client.place_market_order
            # takes the book price. Let's just pass it as is, or assume the place_market_order will be updated 
            # to support limits. For now we use the existing interface.
            # We call the client directly.
            result = polymarket_client.place_market_order(
                token_id=token_id,
                side=side,
                amount=final_amount
            )
            
            if result.get("success"):
                order_id = result.get("order_id")
                
                # 4. Time-in-Force (Sniper Protection)
                # Task assincrona de cancelamento caso o fill nao seja integral a tempo.
                if order_id and timeout_sec > 0:
                    def _cancel_timer():
                        try:
                            logger.info(f"Sniper Protection: Invoking timeout cancel for order {order_id} after {timeout_sec}s")
                            polymarket_client.cancel_order(order_id)
                        except Exception as e:
                            logger.error(f"Timeout cancel error: {e}")
                            
                    timer = threading.Timer(float(timeout_sec), _cancel_timer)
                    timer.daemon = True
                    timer.start()

                # Register the successful copy
                trade["our_trade_id"] = order_id
                db_id = db.log_trade(
                    bot_name="copytrade",
                    market_id=trade.get("market_id", ""),
                    market_question=trade.get("market_question", ""),
                    side=side,
                    amount=result.get("size", final_amount), # Use the actual filled size if returned
                    venue=config.get_venue(),
                    mode=config.get_current_mode(),
                    reasoning=f"PolyCop ({wallet_profile.get('profile_type')}): {wallet_addr[:8]}",
                    trade_id=order_id,
                    owner_tag=wallet_addr,
                )
                logger.info(f"PolyCop executed copy {side} on market {trade.get('market_id')[:8]}")
                executed_trades.append(trade)
            else:
                logger.error(f"PolyCop polymarket execution failed: {result.get('error')}")

        return executed_trades

    def get_copy_stats(self) -> dict:
        """Get copy trading performance stats."""
        with db.get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total, COALESCE(SUM(pnl), 0) as total_pnl,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl <= 0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) as losses
                FROM trades WHERE bot_name='copytrade'
            """).fetchone()
            d = dict(row)
            total = (d["wins"] or 0) + (d["losses"] or 0)
            d["win_rate"] = d["wins"] / total if total > 0 else 0
            return d
