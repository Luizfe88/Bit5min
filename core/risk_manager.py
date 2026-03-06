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
        self.notified_global_stop = False
        self.notified_bot_pauses: Dict[str, float] = {}  # {bot_name_reason: last_notify_time}
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
        # Limites baseados no capital total da arena
        # REGRA PRINCIPAL:
        #   - Bot individual: pausa quando perda >= 15% do capital total
        #   - Global (todos os bots): para TUDO quando perda >= 50% do capital total

        pct_trade = config.MAX_POSITION_PCT_OF_BALANCE  # 0.02
        pct_bot = 0.15
        pct_global = config.MAX_TOTAL_POSITION_PCT_OF_BALANCE  # 0.50

        # ── NOVOS LIMITES DE PERDA ──────────────────────────────────────
        pct_loss_bot = config.MAX_LOSS_PCT_PER_BOT    # 15% do capital total → pausa bot
        pct_loss_global = config.MAX_LOSS_PCT_TOTAL   # 50% do capital total → para TUDO

        limits = {
            "profile": "Custom ($10k Setup)",
            "max_trade_size": max(0.90, round(bankroll * pct_trade, 2)),
            "max_pos_per_bot": max(1.20, round(bankroll * pct_bot, 2)),
            "max_global_position": max(2.50, round(bankroll * pct_global, 2)),
            # Perda máxima por bot = 15% do capital TOTAL da arena
            "max_daily_loss_per_bot": round(bankroll * pct_loss_bot, 2),
            # Perda máxima global = 50% do capital TOTAL da arena
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

        logger.debug(
            f"[RiskManager] Limites calculados | capital=${bankroll:.2f} | "
            f"loss/bot=${limits['max_daily_loss_per_bot']:.2f} ({pct_loss_bot*100:.0f}%) | "
            f"loss/global=${limits['max_daily_loss_global']:.2f} ({pct_loss_global*100:.0f}%)"
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
        """ÚNICO lugar onde você verifica risco agora.

        Regras de pausa:
          - Bot individual: pausa se perda >= 15% do capital total da arena
          - Global (todos): bloqueia TUDO se perda >= 50% do capital total da arena
        """
        # Atualiza banca com o capital total real sempre que necessário
        if time.time() - self.last_update > 30:
            self.update_bankroll(self._get_current_bankroll())

        limits = self.limits

        # 1. Check Duplicates — evita que o mesmo bot abra múltiplas posições no mesmo mercado
        if market:
            market_id = market.get("id") or market.get("market_id")
            if market_id:
                for pos in self.open_positions.values():
                    if pos.bot_name == bot_name and pos.market_id == market_id:
                        return False, "duplicate_position"

        # 2. Tamanho mínimo
        if amount < config.get_min_trade_amount():
            return False, "amount_below_minimum"

        # ── CHECK 3: PnL DIÁRIO GLOBAL (Net PnL + Floating) ───────────────────
        # Checado ANTES do limite por bot para bloquear tudo se o capital cair 15%
        
        # 3a. PnL Realizado Hoje (Líquido: Lucros - Perdas)
        realized_net = db.get_daily_net_pnl(self.mode)
        
        # 3b. PnL Flutuante (Estimado se possível)
        floating = 0.0
        if self.open_positions:
            # Em can_place_trade, talvez não tenhamos market_prices frescos passados por argumento,
            # mas podemos usar o capital total vs banca inicial como proxy rápido se necessário.
            # Aqui, para can_place_trade, focamos no Realizado Net + o que o db já sabe das posições abertas.
            # No entanto, o melhor é usar o realized_net como base sólida.
            pass

        total_pnl_daily = realized_net # + floating
        
        # Se o PnL total for negativo e atingir 15% da banca
        if total_pnl_daily <= -limits["max_daily_loss_global"]:
            self._handle_global_stop(
                abs(total_pnl_daily),
                limits["max_daily_loss_global"],
            )
            return False, "daily_loss_global"

        # ── CHECK 4: PnL DIÁRIO POR BOT (Net PnL Realizado) ──────────────────
        daily_bot_pnl = db.get_bot_daily_net_pnl(bot_name, self.mode)
        if daily_bot_pnl <= -limits["max_daily_loss_per_bot"]:
            self._handle_pause(
                bot_name,
                "daily_loss_per_bot",
                abs(daily_bot_pnl),
                limits["max_daily_loss_per_bot"],
            )
            return False, "daily_loss_per_bot"

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

    def calculate_sl_tp(
        self,
        fill_price: float,
        enable_sl_tp: bool,
        sl_pct: float,
        tp_pct: float,
        side: str = "yes",
        trailing_enabled: bool = False,
        trailing_distance: float = 0.045,
    ) -> Dict[str, Optional[float]]:
        """
        Calcula os valores de SL e TP sempre referenciados ao preço do YES (Market Price).
        fill_price: preço do token comprado (YES price se side='yes', NO token price se side='no').
        Os alvos gerados são sempre em termos de YES price para o Banco de Dados.
        """
        sl_price = None
        tp_price = None

        # Garantir que as porcentagens sejam magnitudes positivas
        sl_pct = abs(sl_pct)
        tp_pct = abs(tp_pct)

        if enable_sl_tp and fill_price > 0:
            if side.lower() == "yes":
                # Lado YES: Lucra com a subida, perde com a queda
                sl_price = fill_price * (1 - sl_pct)
                tp_price = fill_price * (1 + tp_pct)

            elif side.lower() == "no":
                # 1. Encontra o custo real investido no lado NO
                cost_no = 1.0 - fill_price

                # 2. Calcula o valor alvo do contrato NO baseado nas porcentagens
                # Se bater no SL, o valor do contrato NO CAIU. Se bater no TP, SUBIU.
                sl_value_no = cost_no * (1 - sl_pct)
                tp_value_no = cost_no * (1 + tp_pct)

                # 3. Converte de volta para a referência de preço do YES (Universal) para o Banco de Dados
                sl_price = 1.0 - sl_value_no  # acima da entrada YES → gatilho de stop
                tp_price = 1.0 - tp_value_no  # abaixo da entrada YES → gatilho de lucro

            # Clamp de segurança
            if sl_price is not None:
                sl_price = max(0.001, min(0.999, sl_price))
            if tp_price is not None:
                tp_price = max(0.001, min(0.999, tp_price))

        return {"sl_price": sl_price, "tp_price": tp_price}

    def _handle_pause(self, bot_name: str, reason: str, current: float, limit: float):
        """Pausa um bot individual que atingiu 15% de perda do capital total."""
        # Cooldown de 1 hora para notificações do mesmo bot/motivo
        key = f"{bot_name}_{reason}"
        last_notify = self.notified_bot_pauses.get(key, 0)
        if time.time() - last_notify < 3600:
            return

        pct_used = (current / self.bankroll * 100) if self.bankroll else 0
        logger.warning(
            f"⏸️  [{bot_name}] PAUSADO — perda ${current:.2f} "
            f"({pct_used:.1f}% do capital total) >= limite ${limit:.2f} (15%)"
        )
        if self.telegram:
            self.telegram.notify_bot_paused(
                bot_name, reason, loss_amount=current, max_loss=limit
            )
            # Track notification time to allow cooldowns if needed
            self.notified_bot_pauses[f"{bot_name}_{reason}"] = time.time()

    def _handle_global_stop(self, current: float, limit: float):
        """Para TODOS os bots quando a perda global atinge o limite do capital total."""
        pct_used = (current / self.bankroll * 100) if self.bankroll else 0
        pct_limit = config.MAX_LOSS_PCT_TOTAL * 100
        logger.critical(
            f"🛑 PARADA GLOBAL — perda total ${current:.2f} "
            f"({pct_used:.1f}% do capital) >= limite ${limit:.2f} ({pct_limit:.0f}%). "
            f"NENHUM novo trade será aberto."
        )
        if self.notified_global_stop:
            return

        if self.telegram:
            try:
                pct_limit = config.MAX_LOSS_PCT_TOTAL * 100
                msg = (
                    f"🚨 <b>PARADA GLOBAL DE EMERGÊNCIA</b> 🚨\n"
                    f"Perda acumulada: <b>${current:.2f}</b> ({pct_used:.1f}% do capital)\n"
                    f"Limite: <b>${limit:.2f}</b> ({pct_limit:.0f}%)\n"
                    f"Todos os bots bloqueados para novos trades."
                )
                self.telegram.send_message(msg)
                self.notified_global_stop = True
            except Exception as _e:
                logger.error(f"Falha ao enviar alerta global Telegram: {_e}")

    def _get_current_bankroll(self):
        try:
            return config.PAPER_STARTING_BALANCE
        except:
            return 13.06

    def reset_daily(self):
        db.reset_arena_day(self.mode)
        self.notified_global_stop = False
        self.notified_bot_pauses = {}
        logger.info("RiskManager → daily stats reset após evolução")

    def get_floating_pnl(self, market_prices: Dict[str, dict]) -> float:
        """
        Calcula o PnL flutuante (não realizado) de todas as posições abertas.
        market_prices: Dict[market_id] -> {'current_price': float} (preço do YES)
        """
        total_floating_pnl = 0.0
        for trade_id, pos in self.open_positions.items():
            m_state = market_prices.get(pos.market_id)
            if not m_state or "current_price" not in m_state:
                continue
            
            current_yes = float(m_state["current_price"])
            side = pos.direction.lower()
            
            if pos.shares > 0:
                if side == "yes":
                    # PnL = (Preço Atual - Preço Entrada) * Quantidade
                    pnl = (current_yes - pos.entry_price) * pos.shares
                else:
                    # NO: Ganhamos quando YES cai. 
                    # Preço Token NO aproximado = 1.0 - Preço YES
                    # PnL = (Entry_YES - Current_YES) * Quantidade
                    entry_yes = 1.0 - pos.entry_price
                    pnl = (entry_yes - current_yes) * pos.shares
                
                total_floating_pnl += pnl
        
        return total_floating_pnl

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
        sl_val = f"{pos.sl_price:.3f}" if pos.sl_price is not None else "None"
        tp_val = f"{pos.tp_price:.3f}" if pos.tp_price is not None else "None"
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
        CATRACA (RATCHET): Atualiza o trailing stop de acordo com o preço atual do YES.
        Regra de Ouro:
          YES -> SL só pode SUBIR  (nunca diminuir).
          NO  -> SL só pode DESCER (nunca aumentar).
        Isso garante que lucros travados nunca sejam devolvidos.
        """
        if not pos.trailing_enabled or pos.sl_price is None:
            return

        side = pos.direction.lower()
        dist = getattr(pos, "trailing_distance", 0.025)

        if pos.tp_triggered:
            if side == "yes":
                # Candidato = current_price - trailing_distance
                candidate = current_price - dist
                # CATRACA: só avança se for melhor (mais alto)
                if candidate > pos.sl_price:
                    pos.sl_price = candidate
                    db.update_position_sl_tp(
                        trade_id=pos.trade_id, sl_price=pos.sl_price, tp_triggered=True
                    )
                    logger.info(
                        f"{Colors.CYAN}[TRAILING UP]{Colors.RESET} "
                        f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                        f"SL ratcheted to {Colors.GREEN}{pos.sl_price:.4f}{Colors.RESET} "
                        f"(YES={current_price:.4f})"
                    )
            else:
                # NO: Lucro aumenta quando YES CAI. SL é teto — só pode DESCER.
                # Candidato = current_price + trailing_distance
                candidate = current_price + dist
                # CATRACA: só avança se for melhor (mais baixo)
                if candidate < pos.sl_price:
                    pos.sl_price = candidate
                    db.update_position_sl_tp(
                        trade_id=pos.trade_id, sl_price=pos.sl_price, tp_triggered=True
                    )
                    logger.info(
                        f"{Colors.CYAN}[TRAILING DOWN]{Colors.RESET} "
                        f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                        f"SL ratcheted to {Colors.RED}{pos.sl_price:.4f}{Colors.RESET} "
                        f"(YES={current_price:.4f})"
                    )

    def check_sl_tp(
        self, market_prices: Dict[str, dict]
    ) -> List[tuple[OpenPosition, str, float]]:
        """
        Verifica se as posições abertas atingiram SL ou TP.
        A comparação agora é Side-Aware usando o preço do YES.
        """
        exits = []
        for trade_id, pos in list(self.open_positions.items()):
            market_id = pos.market_id
            m_state = market_prices.get(market_id)

            if not m_state or "current_price" not in m_state:
                continue

            # SEMPRE usamos o preço do YES como referência de mercado
            current_yes = float(m_state["current_price"])
            side = pos.direction.lower()

            # 1. SLIPPAGE/SAFETY GUARD (Preço inválido)
            if current_yes <= 0 or current_yes >= 1.0:
                continue

            # 1.5. TIME-PROFIT EXIT (50 minutos = 3000 segundos)
            # Fecha se o tempo de hold >= 50 min e o PnL atual for maior que zero
            hold_time_seconds = time.time() - pos.entry_time
            if hold_time_seconds >= 3000:
                current_pnl = 0.0
                if pos.shares > 0:
                    if side == "yes":
                        current_pnl = (current_yes - pos.entry_price) * pos.shares
                    else:
                        entry_yes_calc = 1.0 - pos.entry_price
                        current_pnl = (entry_yes_calc - current_yes) * pos.shares
                
                if current_pnl > 0:
                    exits.append((pos, "TIME-PROFIT EXIT", current_yes))
                    continue

            # 2. BREAKEVEN AUTOMÁTICO (Mantido em termos de YES price)
            if not pos.tp_triggered and pos.tp_price is not None:
                # Lógica de breakeven simplificada: 50% do caminho.
                # Para YES: (TP - Entry). Para NO: (Entry - TP).
                if side == "yes" and not getattr(pos, "breakeven_triggered", False):
                    # Entry_price aqui é o Token Price (YES).
                    entry_yes = pos.entry_price
                    if pos.tp_price > entry_yes:
                        trigger = entry_yes + (pos.tp_price - entry_yes) * 0.5
                        if current_yes >= trigger:
                            pos.sl_price = entry_yes
                            setattr(pos, "breakeven_triggered", True)
                            db.update_position_sl_tp(
                                trade_id=pos.trade_id, sl_price=pos.sl_price
                            )
                elif side == "no" and not getattr(pos, "breakeven_triggered", False):
                    # Entry_price aqui é o Token Price (NO). Converter para YES.
                    entry_yes = 1.0 - pos.entry_price
                    if pos.tp_price < entry_yes:
                        # Para NO, lucro é queda. Trigger = entry - 50% da queda até TP.
                        trigger = entry_yes - (entry_yes - pos.tp_price) * 0.5
                        if current_yes <= trigger:
                            pos.sl_price = entry_yes
                            setattr(pos, "breakeven_triggered", True)
                            db.update_position_sl_tp(
                                trade_id=pos.trade_id, sl_price=pos.sl_price
                            )

            # 3. TRAILING UPDATE
            if pos.trailing_enabled:
                self.update_trailing_tp(pos, current_yes)

            # 4. GESTÃO DE SAÍDA (SL / TP)
            # Agora com Operadores Invertidos conforme lado para referência YES

            if side == "yes":
                # YES: SL (abaixo), TP (acima)
                if pos.sl_price is not None and current_yes <= pos.sl_price:
                    gap_warn = " [GAP]" if current_yes < pos.sl_price - 0.05 else ""
                    label = ("Trailing Exit" if pos.tp_triggered else "SL") + gap_warn
                    exits.append((pos, label, current_yes))
                elif (
                    not pos.tp_triggered
                    and pos.tp_price is not None
                    and current_yes >= pos.tp_price
                ):
                    # ── GATILHO TP -> TRAILING (YES) ──────────────────────────────
                    pos.tp_triggered = True
                    pos.trailing_enabled = True
                    pos.trailing_distance = 0.025

                    # LOCK-IN IMEDIATO (Catraca): garante 80% do lucro acumulado.
                    # Nunca deixa o SL voltar abaixo do entry_price.
                    entry_yes = pos.entry_price  # para YES, entry_price já é YES
                    locked_sl = entry_yes + (current_yes - entry_yes) * 0.80
                    # Catraca inicial: nunca piora o SL existente
                    pos.sl_price = max(pos.sl_price or 0.0, locked_sl)

                    db.update_position_sl_tp(
                        trade_id=pos.trade_id, sl_price=pos.sl_price, tp_triggered=True
                    )
                    logger.info(
                        f"{Colors.MAGENTA}🔥 TP TRIGGERED (YES){Colors.RESET} "
                        f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                        f"at YES={current_yes:.4f}. "
                        f"Lock-in SL={Colors.GREEN}{pos.sl_price:.4f}{Colors.RESET} "
                        f"(80% lucro garantido). Trailing ON."
                    )
            else:
                # NO: SL (acima), TP (abaixo) - Referência YES Price
                if pos.sl_price is not None and current_yes >= pos.sl_price:
                    gap_warn = " [GAP]" if current_yes > pos.sl_price + 0.05 else ""
                    label = ("Trailing Exit" if pos.tp_triggered else "SL") + gap_warn
                    exits.append((pos, label, current_yes))
                elif (
                    not pos.tp_triggered
                    and pos.tp_price is not None
                    and current_yes <= pos.tp_price
                ):
                    # ── GATILHO TP -> TRAILING (NO) ───────────────────────────────
                    pos.tp_triggered = True
                    pos.trailing_enabled = True
                    pos.trailing_distance = 0.025

                    # LOCK-IN IMEDIATO (Catraca): garante 80% do lucro acumulado.
                    # Para NO, entry_yes = 1 - entry_token. Lucro quando YES cai.
                    entry_yes_no = 1.0 - pos.entry_price
                    locked_sl = entry_yes_no - (entry_yes_no - current_yes) * 0.80
                    # Catraca inicial: nunca piora o SL existente (SL é teto, nunca pode subir)
                    pos.sl_price = min(pos.sl_price or 1.0, locked_sl)

                    db.update_position_sl_tp(
                        trade_id=pos.trade_id, sl_price=pos.sl_price, tp_triggered=True
                    )
                    logger.info(
                        f"{Colors.MAGENTA}🔥 TP TRIGGERED (NO){Colors.RESET} "
                        f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                        f"at YES={current_yes:.4f}. "
                        f"Lock-in SL={Colors.RED}{pos.sl_price:.4f}{Colors.RESET} "
                        f"(80% lucro garantido). Trailing ON."
                    )

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

                # --- 29 SECOND PATCH (Synchronized Sell) ---
                logger.info(f"[{pos.bot_name}] Inciando Synchronized Sell (29 Second Patch) para {pos.market_id}...")
                
                # 1. State Refresh: Cancel All Market Orders
                polymarket_client.cancel_all_market_orders(pos.market_id)
                
                # 2. State Refresh: Pull Exact Balance
                actual_balance = polymarket_client.get_token_balance(pos.token_id)
                
                # 3. Calculate safe sell amount isolated to this owner_tag
                # If balance is less than expected, sell whatever is left to prevent error.
                sell_amount = min(pos.shares, actual_balance) if actual_balance > 0 else 0
                
                if sell_amount <= 0:
                    logger.warning(f"[{pos.bot_name}] Synchronized Sell abortado: Saldo zero ou insuficiente ({actual_balance}) para {getattr(pos, 'owner_tag', 'N/A')}.")
                    # Define success as True to remove from tracking, there's nothing to sell anyway
                    success = True
                else:
                    logger.info(f"[{pos.bot_name}] Synchronized Sell: Tag={getattr(pos, 'owner_tag', 'N/A')}, Target={pos.shares}, Live Balance={actual_balance}. Vendendo={sell_amount}")
                    
                    res = polymarket_client.place_market_order(
                        token_id=pos.token_id, side="sell", amount=sell_amount
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
            # exec_price is the YES price from market (passed from check_sl_tp)
            # pos.entry_price is the TOKEN price (amount / shares)
            side = pos.direction.lower()
            if side == "yes":
                pnl = (exec_price - pos.entry_price) * pos.shares
            else:
                # NO: Ganhamos quando o preço do YES CAI.
                entry_yes = 1.0 - pos.entry_price
                pnl = (entry_yes - exec_price) * pos.shares

            pnl_pct = (pnl / pos.size_usd) * 100 if pos.size_usd else 0

            # Cores para Saída
            pnl_color = Colors.GREEN if pnl >= 0 else Colors.RED
            reason_color = Colors.GREEN if pnl >= 0 else Colors.RED

            log_msg = (
                f"{reason_color}[{reason} HIT]{Colors.RESET} "
                f"{Colors.BOLD}{pos.bot_name}{Colors.RESET} "
                f"closed {pos.direction} "
                f"${pos.size_usd:.2f} @{Colors.YELLOW}{exec_price:.3f}{Colors.RESET} "
                f"(entry {Colors.YELLOW}{pos.entry_price:.3f}{Colors.RESET}) "
                f"PnL: {pnl_color}{('+' if pnl >= 0 else '')}${pnl:.2f} ({pnl_pct:+.1f}%){Colors.RESET}"
            )

            # 1. Log to console
            logger.info(log_msg)

            # 2. Telegram (Robustified)
            try:
                if self.telegram:
                    # Remove ANSI codes for Telegram (simple strip)
                    clean_msg = f"🆔 <b>ID:</b> <code>{pos.id}</code>\n" + (
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
            except Exception as e:
                logger.error(
                    f"Failed to send telegram notification for {pos.trade_id}: {e}"
                )

            # 3. Database Resolution (CRITICAL)
            # Use pos.id (database row ID) not pos.trade_id (Simmer transaction ID)
            if pos.id is None:
                logger.error(
                    f"CRITICAL: Cannot resolve trade - pos.id is None. Trade ID: {pos.trade_id}, Market: {pos.market_id}"
                )
            else:
                try:
                    db.resolve_trade(pos.id, reason.lower(), pnl)
                except Exception as e:
                    logger.error(
                        f"CRITICAL: Failed to resolve trade ID={pos.id} (trade_id={pos.trade_id}) in DB: {e}"
                    )

            if pos.trade_id in self.open_positions:
                del self.open_positions[pos.trade_id]


# Singleton (use em qualquer lugar)
risk_manager = ArenaRiskManager()
