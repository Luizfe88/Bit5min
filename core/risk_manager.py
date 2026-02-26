"""
core/risk_manager.py
RiskManager centralizado para o Arena 10/10
Baseado no ImMike + 100% adaptado ao seu sistema
"""
import logging
import time
import json
from datetime import datetime
from typing import Dict, List, Optional

import config
import db
from telegram_notifier import get_telegram_notifier
from core.position import OpenPosition

logger = logging.getLogger(__name__)

class ArenaRiskManager:
    def __init__(self):
        self.telegram = get_telegram_notifier()
        self.mode = config.get_current_mode()
        self.bankroll = None
        self.limits = {}
        self.last_update = 0
        self.open_positions: Dict[str, OpenPosition] = {}
        logger.info("✅ ArenaRiskManager inicializado")

    def update_bankroll(self, bankroll: float):
        """Atualiza banca e recalcula todos os limites (igual start-arena.ps1)"""
        if self.bankroll is not None and abs(self.bankroll - bankroll) < 0.01:
            return
        self.bankroll = float(bankroll)
        self.limits = self._calculate_dynamic_limits(bankroll)
        self.last_update = time.time()
        logger.info(f"RiskManager atualizado | Banca=${bankroll:.2f} | Perfil={self.limits['profile']}")

    def _calculate_dynamic_limits(self, bankroll: float):
        # Configuração Personalizada para $10k ou outros valores
        # User request: 2% max trade, 15% max per bot, 50% max global
        
        pct_trade = config.MAX_POSITION_PCT_OF_BALANCE # 0.02
        pct_bot = 0.15
        pct_global = config.MAX_TOTAL_POSITION_PCT_OF_BALANCE # 0.50
        
        # Daily loss limits - ajustados proporcionalmente
        pct_loss_bot = 0.05    # 5% por bot
        pct_loss_global = 0.15 # 15% global

        limits = {
            "profile": "Custom ($10k Setup)",
            "max_trade_size": max(0.90, round(bankroll * pct_trade, 2)),
            "max_pos_per_bot": max(1.20, round(bankroll * pct_bot, 2)),
            "max_global_position": max(2.50, round(bankroll * pct_global, 2)),
            "max_daily_loss_per_bot": round(bankroll * pct_loss_bot, 2),
            "max_daily_loss_global": round(bankroll * pct_loss_global, 2),
        }

        # Drawdown Scaling 2.0 (Mantido para segurança)
        initial = self._get_peak_bankroll()
        # Se initial for muito baixo (ex: $10), ajusta para o novo padrão se bankroll for alto
        if initial < 100 and bankroll > 1000:
            initial = bankroll
            
        dd_ratio = bankroll / initial if initial > 0 else 1.0
        if dd_ratio < 0.85:
            limits["max_trade_size"] = round(limits["max_trade_size"] * 0.65, 2)
            limits["max_global_position"] = round(limits["max_global_position"] * 0.70, 2)
            logger.warning(f"🚨 DRAW DOWN CRÍTICO ({(1-dd_ratio)*100:.1f}%) - risco cortado 30-35%")

        return limits

    def _get_peak_bankroll(self):
        try:
            with open("arena_peak.json") as f:
                return json.load(f)["peak"]
        except:
            return self.bankroll or 13.06

    def can_place_trade(self, bot_name: str, amount: float, market: dict = None) -> tuple[bool, str]:
        """ÚNICO lugar onde você verifica risco agora"""
        if time.time() - self.last_update > 30:
            self.update_bankroll(self._get_current_bankroll())

        limits = self.limits

        # 1. Tamanho mínimo
        if amount < config.get_min_trade_amount():
            return False, "amount_below_minimum"

        # 2. Daily loss por bot
        daily_bot = db.get_bot_daily_loss(bot_name, self.mode)
        if daily_bot >= limits["max_daily_loss_per_bot"]:
            self._handle_pause(bot_name, "daily_loss_per_bot", daily_bot, limits["max_daily_loss_per_bot"])
            return False, "daily_loss_per_bot"

        # 3. Daily loss arena
        daily_global = db.get_total_daily_loss(self.mode)
        if daily_global >= limits["max_daily_loss_global"]:
            return False, "daily_loss_global"

        # 4. Posição por bot
        open_bot = db.get_total_open_position_value(bot_name, self.mode)
        if open_bot + amount > limits["max_pos_per_bot"]:
            return False, "max_position_per_bot"

        # 5. Posição global
        open_global = db.get_total_open_position_value_all_bots(self.mode)
        if open_global + amount > limits["max_global_position"]:
            return False, "max_global_position"

        # 6. Spread (mantido do seu código)
        if market and (market.get("p_yes", 0.5) + market.get("p_no", 0.5) > 1.05):
            return False, "high_spread"

        return True, "ok"

    def _handle_pause(self, bot_name: str, reason: str, current: float, limit: float):
        logger.warning(f"[{bot_name}] {reason} → ${current:.2f} >= ${limit:.2f}")
        if self.telegram:
            self.telegram.notify_bot_paused(bot_name, reason, loss_amount=current, max_loss=limit)

    def _get_current_bankroll(self):
        # Tenta ler exatamente como no seu start-arena.ps1
        try:
            # Você pode chamar a mesma função que usa no PowerShell ou deixar o start-arena chamar update_bankroll
            return config.PAPER_STARTING_BALANCE   # ajuste se quiser ler da API aqui
        except:
            return 13.06

    def reset_daily(self):
        db.reset_arena_day(self.mode)
        logger.info("RiskManager → daily stats reset após evolução")

    def get_summary(self):
        self.update_bankroll(self._get_current_bankroll())
        return {
            "bankroll": self.bankroll,
            "profile": self.limits.get("profile"),
            **self.limits,
            "mode": self.mode
        }
        
    def add_position(self, pos: OpenPosition):
        """Registra uma nova posição aberta para monitoramento."""
        if not pos.trade_id:
            logger.warning(f"Tentativa de registrar posição sem trade_id: {pos}")
            return
        self.open_positions[pos.trade_id] = pos
        logger.info(f"RiskManager: Posição registrada {pos.bot_name} {pos.direction} em {pos.market_id} (SL: {pos.sl_price}, TP: {pos.tp_price})")

    def check_sl_tp(self, market_prices: Dict[str, dict]) -> List[tuple[OpenPosition, str, float]]:
        """
        Verifica SL/TP para todas as posições abertas.
        Retorna lista de (posicao, razao, preco_atual).
        market_prices: dict {market_id: {'current_price': price, ...}}
        """
        exits = []
        for trade_id, pos in list(self.open_positions.items()):
            market_data = market_prices.get(pos.market_id)
            if not market_data:
                continue

            # Determinar preço atual do token que possuímos
            current_yes_price = market_data.get("current_price")
            if current_yes_price is None:
                continue
            
            try:
                current_yes_price = float(current_yes_price)
            except ValueError:
                continue
            
            if pos.direction == "NO":
                # Preço do NO = 1 - Preço do YES
                my_price = 1.0 - current_yes_price
            else:
                my_price = current_yes_price
            
            # Checar SL
            if pos.sl_price is not None:
                # Se preço caiu abaixo do SL
                if my_price <= pos.sl_price:
                    exits.append((pos, "SL", my_price))
                    continue
            
            # Checar TP
            if pos.tp_price is not None:
                # Se preço subiu acima do TP
                if my_price >= pos.tp_price:
                    exits.append((pos, "TP", my_price))
                    continue
        
        return exits

    def close_position(self, pos: OpenPosition, reason: str, current_price: float):
        """Fecha a posição no mercado secundário (simulado para paper, real para live)."""
        logger.info(f"[{reason} HIT] {pos.bot_name} fechando {pos.direction} em {pos.market_id}. Entry: {pos.entry_price:.3f}, Now: {current_price:.3f}")
        
        success = False
        pnl = 0.0
        
        try:
            if self.mode == "live":
                import polymarket_client
                # Em live, precisamos do token_id para vender
                if not pos.token_id:
                     logger.error(f"Erro ao fechar {pos.trade_id}: token_id ausente")
                     return

                # Vender shares no mercado (side=sell)
                # Nota: Assumindo que place_market_order suporta side='sell' para fechar long
                res = polymarket_client.place_market_order(
                    token_id=pos.token_id,
                    side="sell", 
                    amount=pos.shares 
                )
                if res.get("success"):
                    success = True
                    # Calcular PnL real baseado no preço de execução
                    exec_price = float(res.get("price", current_price))
                    pnl = (exec_price - pos.entry_price) * pos.shares
                else:
                    logger.error(f"Falha ao fechar posição live {pos.trade_id}: {res.get('error')}")

            else: 
                # Paper trading: simula fechamento imediato ao preço atual
                success = True
                pnl = (current_price - pos.entry_price) * pos.shares
        
        except Exception as e:
            logger.error(f"Exceção ao fechar posição {pos.trade_id}: {e}")
        
        if success:
            # Calcular % PnL
            pnl_pct = (pnl / pos.size_usd) * 100 if pos.size_usd else 0
            
            # Log visível conforme pedido
            log_msg = (
                f"[{reason} HIT] {pos.bot_name} closed {pos.direction} "
                f"${pos.size_usd:.2f} @{current_price:.2f} (entry {pos.entry_price:.2f}) "
                f"PnL: {('+' if pnl >= 0 else '')}${pnl:.2f} ({pnl_pct:+.1f}%)"
            )
            logger.info(log_msg)
            if self.telegram:
                self.telegram.send_message(log_msg)

            # Atualizar DB
            db.resolve_trade(pos.trade_id, reason.lower(), pnl)
            
            # Remover da lista de posições abertas
            if pos.trade_id in self.open_positions:
                del self.open_positions[pos.trade_id]

# Singleton (use em qualquer lugar)
risk_manager = ArenaRiskManager()
