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


# ANSI Colors for better visibility in logs
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


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
        logger.info(
            f"RiskManager atualizado | Banca=${bankroll:.2f} | Perfil={self.limits['profile']}"
        )

    def _calculate_dynamic_limits(self, bankroll: float):
        # Configuração Personalizada para $10k ou outros valores

        pct_trade = config.MAX_POSITION_PCT_OF_BALANCE  # 0.02
        pct_bot = 0.15
        pct_global = config.MAX_TOTAL_POSITION_PCT_OF_BALANCE  # 0.50

        # Daily loss limits - ajustados proporcionalmente
        pct_loss_bot = 0.05  # 5% por bot
        pct_loss_global = 0.15  # 15% global

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
            limits["max_global_position"] = round(
                limits["max_global_position"] * 0.70, 2
            )
            logger.warning(
                f"🚨 DRAW DOWN CRÍTICO ({(1 - dd_ratio) * 100:.1f}%) - risco cortado 30-35%"
            )

        return limits

    def _get_peak_bankroll(self):
        try:
            with open("arena_peak.json") as f:
                return json.load(f)["peak"]
        except:
            return self.bankroll or 13.06

    def can_place_trade(
        self, bot_name: str, amount: float, market: dict = None
    ) -> tuple[bool, str]:
        """ÚNICO lugar onde você verifica risco agora"""
        if time.time() - self.last_update > 30:
            self.update_bankroll(self._get_current_bankroll())

        limits = self.limits

        # 1. Check Duplicates (Novo)
        # Evita que o mesmo bot abra múltiplas posições no mesmo mercado
        if market:
            market_id = market.get("id") or market.get("market_id")
            if market_id:
                for pos in self.open_positions.values():
                    if pos.bot_name == bot_name and pos.market_id == market_id:
                        return False, "duplicate_position"

        # 2. Tamanho mínimo
        if amount < config.get_min_trade_amount():
            return False, "amount_below_minimum"

        # 3. Daily loss por bot
        daily_bot = db.get_bot_daily_loss(bot_name, self.mode)
        if daily_bot >= limits["max_daily_loss_per_bot"]:
            self._handle_pause(
                bot_name,
                "daily_loss_per_bot",
                daily_bot,
                limits["max_daily_loss_per_bot"],
            )
            return False, "daily_loss_per_bot"

        # 4. Daily loss arena
        daily_global = db.get_total_daily_loss(self.mode)
        if daily_global >= limits["max_daily_loss_global"]:
            return False, "daily_loss_global"

        # 5. Posição por bot
        open_bot = db.get_total_open_position_value(bot_name, self.mode)
        if open_bot + amount > limits["max_pos_per_bot"]:
            return False, "max_position_per_bot"

        # 6. Posição global
        open_global = db.get_total_open_position_value_all_bots(self.mode)
        if open_global + amount > limits["max_global_position"]:
            return False, "max_global_position"

        # 7. Spread (mantido do seu código)
        if market and (market.get("p_yes", 0.5) + market.get("p_no", 0.5) > 1.05):
            return False, "high_spread"

        return True, "ok"

    def _handle_pause(self, bot_name: str, reason: str, current: float, limit: float):
        logger.warning(f"[{bot_name}] {reason} → ${current:.2f} >= ${limit:.2f}")
        if self.telegram:
            self.telegram.notify_bot_paused(
                bot_name, reason, loss_amount=current, max_loss=limit
            )

    def _get_current_bankroll(self):
        try:
            return config.PAPER_STARTING_BALANCE
        except:
            return 13.06

    def reset_daily(self):
        db.reset_arena_day(self.mode)
        logger.info("RiskManager → daily stats reset após evolução")

    def get_current_total_exposure(self) -> float:
        """Get current total exposure across all open positions (in dollars).

        Returns:
            Total value of all open positions across all bots
        """
        try:
            return db.get_total_open_position_value_all_bots(self.mode)
        except Exception as e:
            logger.warning(f"Error getting total exposure: {e}, defaulting to 0")
            return 0.0

    def get_current_exposure_percent(self, total_capital: float) -> float:
        """Get current total exposure as percentage of capital.

        Args:
            total_capital: Total available capital in dollars

        Returns:
            Current exposure as percentage (0.0-1.0)
        """
        if total_capital <= 0:
            return 0.0

        current_exposure = self.get_current_total_exposure()
        return current_exposure / total_capital

    def get_summary(self):
        self.update_bankroll(self._get_current_bankroll())
        return {
            "bankroll": self.bankroll,
            "profile": self.limits.get("profile"),
            **self.limits,
            "mode": self.mode,
        }

    def add_position(self, pos: OpenPosition):
        """Registra uma nova posição aberta para monitoramento."""
        if not pos.trade_id:
            logger.warning(f"Tentativa de registrar posição sem trade_id: {pos}")
            return

        # FIX: Validate Shares > 0 (Requested Validation)
        if pos.shares <= 0:
            logger.warning(
                f"[{pos.bot_name}] Ignorando registro de posição com shares={pos.shares} no {pos.market_id}"
            )
            return

        # 1. Duplicate Check (Double Safety)
        for existing in self.open_positions.values():
            if (
                existing.bot_name == pos.bot_name
                and existing.market_id == pos.market_id
            ):
                logger.warning(
                    f"[{pos.bot_name}] Ignorando registro de duplicata no {pos.market_id}"
                )
                return

        # 2. Grace Period Setup
        if config.GRACE_PERIOD_SECONDS > 0:
            pos.grace_period_ends_at = time.time() + config.GRACE_PERIOD_SECONDS

        self.open_positions[pos.trade_id] = pos

        # Logging Claro e Colorido conforme solicitado
        grace_str = (
            f" grace={config.GRACE_PERIOD_SECONDS}s" if pos.grace_period_ends_at else ""
        )

        # Cores para SL/TP
        sl_val = f"{pos.sl_price:.3f}" if pos.sl_price else "None"
        tp_val = f"{pos.tp_price:.3f}" if pos.tp_price else "None"
        sl_str = f"SL={Colors.RED}{sl_val}{Colors.RESET}"
        tp_str = f"TP={Colors.GREEN}{tp_val}{Colors.RESET}"

        # Cor para Side (YES=Green, NO=Red)
        side_color = Colors.GREEN if pos.direction.upper() == "YES" else Colors.RED

        logger.info(
            f"{Colors.BLUE}[ENTRY]{Colors.RESET} "
            f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
            f"{side_color}{pos.direction}{Colors.RESET} "
            f"@{Colors.YELLOW}{pos.entry_price:.3f}{Colors.RESET} "
            f"(size ${pos.size_usd:.2f}) "
            f"{sl_str} {tp_str}{grace_str}"
        )

    def update_trailing_tp(self, pos: OpenPosition, current_price: float):
        """
        Atualiza o TP dinamicamente se trailing estiver habilitado.
        current_price: O preço ATUAL da posição que possuímos (0.0-1.0).
                       Se temos NO, current_price já deve ser (1 - YES_price).
        """
        if not pos.trailing_enabled or pos.trailing_distance is None:
            return

        # Só começa a trailing depois do grace period (se houver)
        if pos.grace_period_ends_at and time.time() < pos.grace_period_ends_at:
            return

        # Inicialização Segura
        if pos.tp_price is None:
            # Inicializa o Trailing Floor abaixo do preço atual
            pos.tp_price = max(0.01, current_price - pos.trailing_distance)
            logger.info(
                f"{Colors.MAGENTA}[TRAILING INIT]{Colors.RESET} "
                f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                f"inicializou TP em {Colors.GREEN}{pos.tp_price:.3f}{Colors.RESET} "
                f"(Price: {Colors.YELLOW}{current_price:.3f}{Colors.RESET})"
            )
            return

        old_tp = pos.tp_price
        dist = pos.trailing_distance
        step = pos.trailing_step or 0.005

        # Lógica Unificada: Trailing TP é um FLOOR que sobe com o preço
        # Novo TP potencial = Preço Atual - Distância
        potential_tp = current_price - dist

        # O TP só pode subir (nunca descer) para proteger lucro
        # Se o preço subiu muito, o potential_tp vai ser maior que o old_tp
        if potential_tp > old_tp:
            # Aplica step mínimo para evitar spam de logs/updates
            if (potential_tp - old_tp) >= step:
                pos.tp_price = potential_tp
                logger.info(
                    f"{Colors.MAGENTA}[TRAILING]{Colors.RESET} "
                    f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                    f"atualizou TP de {Colors.YELLOW}{old_tp:.3f}{Colors.RESET} -> {Colors.GREEN}{pos.tp_price:.3f}{Colors.RESET} "
                    f"(Price: {Colors.YELLOW}{current_price:.3f}{Colors.RESET})"
                )

    def check_sl_tp(
        self, market_prices: Dict[str, dict]
    ) -> List[tuple[OpenPosition, str, float]]:
        """
        Verifica SL/TP para todas as posições abertas.
        Retorna lista de (posicao, razao, preco_atual).
        market_prices: dict {market_id: {'current_price': price, ...}}
        """
        exits = []
        now = time.time()

        for trade_id, pos in list(self.open_positions.items()):
            # 1. Grace Period Check
            if pos.grace_period_ends_at and now < pos.grace_period_ends_at:
                continue

            market_data = market_prices.get(pos.market_id)
            if not market_data:
                continue

            # Determinar preço atual do token que possuímos
            # API Simmer retorna 'current_price' como probabilidade do YES
            current_yes_price = market_data.get("current_price")
            if current_yes_price is None:
                continue

            try:
                current_yes_price = float(current_yes_price)
            except ValueError:
                continue

            # Se tenho shares NO, meu preço é 1 - YES
            if pos.direction == "NO":
                my_price = 1.0 - current_yes_price
            else:
                my_price = current_yes_price

            # Bounds check
            my_price = max(0.001, min(0.999, my_price))

            # --- TRAILING TP UPDATE ---
            if pos.trailing_enabled:
                self.update_trailing_tp(pos, my_price)

            # Checar SL (Fixo)
            if pos.sl_price is not None:
                if my_price <= pos.sl_price:
                    exits.append((pos, "SL", my_price))
                    continue

            # Checar TP
            if pos.tp_price is not None:
                if pos.trailing_enabled:
                    # Trailing TP atua como um Stop Loss dinâmico (Floor)
                    # Se o preço cair abaixo do TP (que subiu), sai.
                    # Isso garante que saímos com lucro garantido pelo trailing
                    if my_price <= pos.tp_price:
                        exits.append((pos, "TP (Trailing)", my_price))
                        continue
                else:
                    # TP Fixo atua como Take Profit (Ceiling)
                    # Se o preço subir acima do TP, sai.
                    if my_price >= pos.tp_price:
                        exits.append((pos, "TP", my_price))
                        continue

        return exits

    def close_position(self, pos: OpenPosition, reason: str, current_price: float):
        """Fecha a posição no mercado secundário (simulado para paper, real para live)."""
        # Pre-log removido para evitar duplicidade com o log detalhado abaixo, ou mantido simplificado
        # logger.info(f"[{reason} HIT] {pos.bot_name} fechando {pos.direction} em {pos.market_id}. Entry: {pos.entry_price:.3f}, Now: {current_price:.3f}")

        success = False
        pnl = 0.0
        exec_price = current_price

        try:
            if self.mode == "live":
                import polymarket_client

                if not pos.token_id:
                    logger.error(f"Erro ao fechar {pos.trade_id}: token_id ausente")
                    return

                res = polymarket_client.place_market_order(
                    token_id=pos.token_id, side="sell", amount=pos.shares
                )
                if res.get("success"):
                    success = True
                    # Em live, usa o preço real de execução
                    exec_price = float(res.get("price", current_price))
                else:
                    logger.error(
                        f"Falha ao fechar posição live {pos.trade_id}: {res.get('error')}"
                    )

            else:
                # Paper trading
                success = True
                # Em paper, usa o preço de mercado detectado (current_price)
                exec_price = current_price

        except Exception as e:
            logger.error(f"Exceção ao fechar posição {pos.trade_id}: {e}")

        if success:
            # PnL Calculation
            # Se tenho shares (unidades), PnL = (Preço Saída - Preço Entrada) * Shares
            # Ex: Comprei 100 shares @ 0.40 (Custo $40). Vendo @ 0.50 (Recebo $50). PnL = (0.50 - 0.40) * 100 = $10.
            pnl = (exec_price - pos.entry_price) * pos.shares

            pnl_pct = (pnl / pos.size_usd) * 100 if pos.size_usd else 0

            # Cores para Saída
            pnl_color = Colors.GREEN if pnl >= 0 else Colors.RED
            # Motivo também ganha cor baseada no resultado (TP geralmente é verde, SL vermelho)
            reason_color = Colors.GREEN if pnl >= 0 else Colors.RED

            log_msg = (
                f"{reason_color}[{reason} HIT]{Colors.RESET} "
                f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                f"closed {pos.direction} "
                f"${pos.size_usd:.2f} @{Colors.YELLOW}{exec_price:.3f}{Colors.RESET} "
                f"(entry {Colors.YELLOW}{pos.entry_price:.3f}{Colors.RESET}) "
                f"PnL: {pnl_color}{('+' if pnl >= 0 else '')}${pnl:.2f} ({pnl_pct:+.1f}%){Colors.RESET}"
            )
            logger.info(log_msg)
            if self.telegram:
                # Remove ANSI codes for Telegram (simple strip)
                clean_msg = (
                    log_msg.replace(Colors.GREEN, "")
                    .replace(Colors.RED, "")
                    .replace(Colors.YELLOW, "")
                    .replace(Colors.BLUE, "")
                    .replace(Colors.MAGENTA, "")
                    .replace(Colors.CYAN, "")
                    .replace(Colors.BOLD, "")
                    .replace(Colors.RESET, "")
                )
                self.telegram.send_message(clean_msg)

            db.resolve_trade(pos.trade_id, reason.lower(), pnl)

            if pos.trade_id in self.open_positions:
                del self.open_positions[pos.trade_id]


# Singleton (use em qualquer lugar)
risk_manager = ArenaRiskManager()
