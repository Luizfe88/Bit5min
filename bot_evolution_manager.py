"""
Bot Evolution Manager - Sistema de evolução baseado em trades resolvidos
Gatilhos: 50 trades globais | Safety net: 24h | Cooldown: 24h
"""

import json
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import config
import db
from bots.base_bot import BaseBot

logger = logging.getLogger(__name__)


class EvolutionTrigger(Enum):
    """Razões para evolução"""
    TRADE_THRESHOLD = "trade_threshold"
    SAFETY_NET = "safety_net"
    MANUAL = "manual"


@dataclass
class EvolutionMetrics:
    """Métricas para análise de evolução"""
    global_trade_count: int
    last_evolution_time: datetime
    time_since_last_evolution: timedelta
    cooldown_active: bool
    can_evolve: bool
    trigger_reason: Optional[EvolutionTrigger]


class BotEvolutionManager:
    """
    Gerencia evolução de bots baseada em:
    - Gatilho principal: 50 trades resolvidos (global)
    - Safety net: máximo 24 horas sem evolução (mesmo sem 50 trades)
    - Cooldown mínimo: 24 horas entre evoluções
    """
    
    def __init__(self, bots_source=None):
        self.global_trade_count = 0
        self.last_evolution_time = None  # Será definido pelo _load_state
        self.evolution_in_progress = False
        self.cooldown_hours = config.EVOLUTION_MIN_HOURS_COOLDOWN
        self.max_time_without_evolution = config.EVOLUTION_INTERVAL_HOURS * 3600
        self.target_trades = config.EVOLUTION_MIN_TRADES
        self.lock = threading.Lock()
        self._bots_source = bots_source  # Função para obter bots ativos
        self._last_status_log = None    # Controla log de status a cada 15 min
        self.status_log_interval = 15 * 60  # 15 minutos em segundos
        self._load_state()
        
        logger.info(f"🧬 BotEvolutionManager iniciado - Target: {self.target_trades} trades, "
                   f"Cooldown: {self.cooldown_hours}h, Safety net: {self.max_time_without_evolution/3600}h")
    
    def _load_state(self):
        """Carrega estado persistente do banco de dados"""
        try:
            # Carrega timestamp da última evolução
            saved_last_evo = db.get_arena_state("last_evolution_time")
            if saved_last_evo:
                self.last_evolution_time = datetime.fromtimestamp(float(saved_last_evo))
            else:
                self.last_evolution_time = datetime.now()

            # --- CORREÇÃO: Carrega contagem de trades do banco de dados ---
            # Conta trades resolvidos APÓS a última evolução
            with db.get_conn() as conn:
                cursor = conn.cursor()
                # Timestamp da última evolução para filtro (em BRT para a query)
                last_evo_ts = self.last_evolution_time.strftime('%Y-%m-%d %H:%M:%S')
                
                # Query: Trades resolvidos com resolved_at > last_evolution_time
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM trades 
                    WHERE outcome IS NOT NULL 
                    AND resolved_at > ?
                """, (last_evo_ts,))
                
                trade_count = cursor.fetchone()[0]
                self.global_trade_count = trade_count
                
            logger.info(f"📊 Estado carregado: Última evolução {self.last_evolution_time}, Trades parciais: {self.global_trade_count}")
                
        except Exception as e:
            logger.error(f"Erro ao carregar estado: {e}")
            self.last_evolution_time = datetime.now()
            self.global_trade_count = 0
    
    def _save_state(self):
        """Salva estado no banco de dados"""
        try:
            # Salva apenas o timestamp da última evolução usando o sistema da arena
            import time
            db.set_arena_state("last_evolution_time", str(self.last_evolution_time.timestamp()))
        except Exception as e:
            logger.error(f"Erro ao salvar estado: {e}")
    
    def increment_trade_counter(self, bot_name: str, trade_result: Dict):
        """
        Incrementa contador global quando um trade é resolvido
        
        Args:
            bot_name: Nome do bot que resolveu o trade
            trade_result: Dict com resultado do trade (pnl, win/loss, etc)
        """
        with self.lock:
            self.global_trade_count += 1
            logger.info(f"📈 Trade resolvido por {bot_name}. Total global: {self.global_trade_count}")
            
            # Salva trade no histórico para análise
            try:
                db.record_resolved_trade(bot_name, trade_result)
            except Exception as e:
                logger.error(f"Erro ao registrar trade: {e}")
            
            self._save_state()
            
            # Avalia se deve iniciar evolução
            self._evaluate_evolution_trigger()

    def check_evolution_triggers(self):
        """Verifica os gatilhos de evolução e inicia se necessário (e.g., safety net)."""
        logger.debug("Verificando gatilhos de evolução (chamada periódica).")
        with self.lock:
            self._evaluate_evolution_trigger()
    
    def get_metrics(self) -> EvolutionMetrics:
        """Retorna métricas atuais do sistema"""
        now = datetime.now()
        
        # Se não há última evolução, considera que está pronto para evoluir
        if self.last_evolution_time is None:
            time_since_last = timedelta(0)  # Tempo zero para database nova
            cooldown_active = False
            trigger_reason = EvolutionTrigger.SAFETY_NET
        else:
            time_since_last = now - self.last_evolution_time
            cooldown_active = time_since_last.total_seconds() < (self.cooldown_hours * 3600)
            
            trigger_reason = None
            if not cooldown_active:
                if self.global_trade_count >= self.target_trades:
                    trigger_reason = EvolutionTrigger.TRADE_THRESHOLD
                elif time_since_last.total_seconds() >= self.max_time_without_evolution:
                    # SAFETY NET: Só engatilha se houver amostragem mínima (50 trades)
                    min_sample = getattr(config, "MIN_SAMPLE_SIZE_EVOLUTION", 50)
                    if self.global_trade_count >= min_sample:
                        trigger_reason = EvolutionTrigger.SAFETY_NET
                    else:
                        # Não define trigger_reason, a evolução será adiada
                        pass
        
        return EvolutionMetrics(
            global_trade_count=self.global_trade_count,
            last_evolution_time=self.last_evolution_time or now,
            time_since_last_evolution=time_since_last,
            cooldown_active=cooldown_active,
            can_evolve=trigger_reason is not None,
            trigger_reason=trigger_reason
        )
    
    def _evaluate_evolution_trigger(self):
        """Avalia se deve iniciar evolução baseado nas regras:
        - Evolui após 50 trades, mas nunca antes de 24h (cooldown)
        - Safety net: após 24h sem evolução, evolui mesmo sem 50 trades (se amostragem mínima atingida)
        """
        if self.evolution_in_progress:
            return
        
        metrics = self.get_metrics()
        
        if not metrics.can_evolve:
            self._log_status_if_due(metrics)
            return
        
        # Inicia evolução em thread separada para não bloquear
        thread = threading.Thread(target=self._trigger_evolution, args=(metrics.trigger_reason,))
        thread.daemon = True
        thread.start()

    def _log_status_if_due(self, metrics: 'EvolutionMetrics'):
        """Loga status da evolução a cada 15 minutos"""
        now = datetime.now()
        if self._last_status_log and (now - self._last_status_log).total_seconds() < self.status_log_interval:
            return  # Ainda não chegou o momento de logar

        self._last_status_log = now
        trades_remaining = max(0, self.target_trades - metrics.global_trade_count)

        if metrics.cooldown_active:
            remaining_cooldown = timedelta(hours=self.cooldown_hours) - metrics.time_since_last_evolution
            # Formata como HH:MM sem microsegundos
            remaining_str = str(remaining_cooldown).split('.')[0]
            logger.info(
                f"⏱️  Cooldown ativo. Próxima evolução em: {remaining_str} | "
                f"Trades: {metrics.global_trade_count}/{self.target_trades} "
                f"(faltam {trades_remaining})"
            )
        else:
            time_until_safety = timedelta(seconds=self.max_time_without_evolution) - metrics.time_since_last_evolution
            safety_str = str(time_until_safety).split('.')[0]
            
            min_sample = getattr(config, "MIN_SAMPLE_SIZE_EVOLUTION", 50)
            sample_status = f" | Amostragem: {metrics.global_trade_count}/{min_sample}" if metrics.time_since_last_evolution.total_seconds() >= self.max_time_without_evolution else ""
            
            logger.info(
                f"⏳ Aguardando trades. {metrics.global_trade_count}/{self.target_trades} "
                f"(faltam {trades_remaining}) | Safety net em: {safety_str}{sample_status}"
            )
            
            if metrics.time_since_last_evolution.total_seconds() >= self.max_time_without_evolution and metrics.global_trade_count < min_sample:
                logger.warning(f"⚠️  EVOLUÇÃO ADIADA: Tempo esgotado ({self.max_time_without_evolution/3600}h) mas amostragem insuficiente ({metrics.global_trade_count}/{min_sample} trades).")
    
    def _trigger_evolution(self, trigger_reason: EvolutionTrigger):
        """Inicia processo de evolução"""
        with self.lock:
            if self.evolution_in_progress:
                return
            self.evolution_in_progress = True
        
        logger.info(f"🧬 Iniciando evolução de bots (razão: {trigger_reason.value})")
        logger.info(f"📊 Métricas atuais: {self.global_trade_count} trades, "
                   f"tempo desde última evolução: {datetime.now() - self.last_evolution_time}")
        
        # 🔒 PROTEÇÃO: Não evoluir com 0 trades executados
        if self.global_trade_count == 0:
            logger.warning("🚫 Nenhum trade executado - evolução cancelada")
            logger.info("💡 Aguardando trades serem executados antes da primeira evolução")
            self.evolution_in_progress = False
            return
        
        try:
            # Obtém bots ativos
            active_bots = self._get_active_bots()
            if not active_bots:
                logger.warning("Nenhum bot ativo para evolução")
                return
            
            # Analisa performance e seleciona sobreviventes
            rankings = self._analyze_bot_performance(active_bots)
            survivors = self._select_survivors(rankings)
            
            # Cria novos bots evoluídos
            new_bots = self._create_evolved_bots(survivors, active_bots)
            
            # Atualiza configurações no banco
            self._update_bot_configs(survivors, new_bots)
            
            # Registra evento de evolução
            self._log_evolution_event(trigger_reason, rankings, survivors, new_bots)
            
            # Atualiza estado
            with self.lock:
                self.last_evolution_time = datetime.now()
                self.global_trade_count = 0
                self.evolution_in_progress = False
                self._save_state()
            
            logger.info(f"✅ Evolução concluída. Próxima evolução em {self.cooldown_hours}h.")
            
        except Exception as e:
            logger.error(f"❌ Erro na evolução: {e}", exc_info=True)
            with self.lock:
                self.evolution_in_progress = False
    
    def _get_active_bots(self) -> List[BaseBot]:
        """Obtém lista de bots ativos"""
        if self._bots_source:
            return self._bots_source()
        # Fallback para manter compatibilidade
        return []
    
    def _analyze_bot_performance(self, bots: List[BaseBot]) -> List[Dict]:
        """Analisa performance de cada bot"""
        rankings = []
        
        for bot in bots:
            try:
                # Obtém performance do último período
                perf = bot.get_performance(hours=self.cooldown_hours)
                trades = perf.get("total_trades", 0)
                pnl = perf.get("total_pnl", 0)
                win_rate = perf.get("win_rate", 0)
                
                # Calcula score ponderado
                # --- MÓDULO 5: Evolução Genética via Brier Score ---
                brier_score = db.get_bot_brier_score(bot.name, hours=self.cooldown_hours * 2)
                
                # Calcula score ponderado
                sample_weight = min(1.0, trades / 20)  # Peso baseado em trades amostragem
                
                # Fitness: PnL + WinRate + Brier Calibration
                # Brier Score varia de 0 (perfeito) a 1 (péssimo). 0.25 é o chute de 50/50.
                # Queremos maximizar o score, então subtraímos o Brier Score.
                # BS_Factor: (0.25 - brier_score) * 4.0 -> Se BS=0, factor=1.0. Se BS=0.25, factor=0. Se BS=0.5, factor=-1.
                calibration_score = (0.25 - brier_score) * 100.0 # Multiplicador de 100 para equiparar ao peso do PnL
                
                score = (pnl * sample_weight) + ((win_rate - 0.5) * 20.0 * sample_weight) + (calibration_score * sample_weight)
                
                rankings.append({
                    "bot": bot,
                    "name": bot.name,
                    "strategy_type": bot.strategy_type,
                    "generation": bot.generation,
                    "pnl": pnl,
                    "win_rate": win_rate,
                    "brier_score": brier_score,
                    "score": score,
                })
                
            except Exception as e:
                logger.error(f"Erro ao analisar {bot.name}: {e}")
                rankings.append({
                    "bot": bot,
                    "name": bot.name,
                    "strategy_type": bot.strategy_type,
                    "generation": bot.generation,
                    "pnl": 0,
                    "win_rate": 0,
                    "trades": 0,
                    "score": -999,
                })
        
        # Ordena por score decrescente
        rankings.sort(key=lambda x: x["score"], reverse=True)
        return rankings
    
    def _select_survivors(self, rankings: List[Dict]) -> List[Dict]:
        """Seleciona bots sobreviventes baseado em performance"""
        survivors_count = getattr(config, 'SURVIVORS_PER_CYCLE', 3)
        survivors = rankings[:survivors_count]
        
        logger.info("🏆 Rankings de Performance:")
        for i, rank in enumerate(rankings):
            status = "SOBREVIVE" if i < survivors_count else "REPLACED"
            logger.info(f"  #{i+1} {rank['name']}: score={rank['score']:+.2f} "
                       f"P&L=${rank['pnl']:.2f}, WR={rank['win_rate']:.1%}, "
                       f"Brier={rank.get('brier_score', 0):.4f} [{status}]")
        
        return survivors
    
    def _create_evolved_bots(self, survivors: List[Dict], all_bots: List[BaseBot]) -> List[Dict]:
        """Cria novos bots evoluídos"""
        new_bots = []
        
        # Identifica bots que serão substituídos
        survivor_names = {s['name'] for s in survivors}
        replaced_bots = [b for b in all_bots if b.name not in survivor_names]
        
        for dead_bot in replaced_bots:
            # Seleciona parent aleatório entre sobreviventes
            parent = survivors[0]["bot"]  # Melhor performer
            
            # Cria bot evoluído (usa lógica existente do arena.py)
            evolved = self._create_evolved_bot_from_parent(parent, dead_bot.strategy_type)
            
            new_bots.append({
                "evolved_bot": evolved,
                "parent": parent.name,
                "replaced": dead_bot.name
            })
            
            logger.info(f"  ⭐ Criado {evolved.name} (de {parent.name})")
        
        return new_bots
    
    def _create_evolved_bot_from_parent(self, parent: BaseBot, strategy_type: str) -> BaseBot:
        """Cria bot evoluído a partir de parent"""
        # Esta função deve integrar com a lógica existente de evolução
        # Por enquanto, retorna uma cópia mutada
        
        # Importa função existente do arena.py
        from arena import create_evolved_bot
        
        # Usa função existente mas com nova geração
        gen_number = parent.generation + 1
        return create_evolved_bot(parent, strategy_type, gen_number)
    
    def _update_bot_configs(self, survivors: List[Dict], new_bots: List[Dict]):
        """Atualiza configurações no banco de dados"""
        try:
            # Retira bots substituídos
            for new_bot_info in new_bots:
                replaced_name = new_bot_info["replaced"]
                db.retire_bot(replaced_name)
            
            # Salva novos bots
            for new_bot_info in new_bots:
                evolved = new_bot_info["evolved_bot"]
                db.save_bot_config(
                    evolved.name,
                    evolved.strategy_type,
                    evolved.generation,
                    evolved.strategy_params,
                    evolved.lineage
                )
                
        except Exception as e:
            logger.error(f"Erro ao atualizar configs: {e}")
    
    def _log_evolution_event(self, trigger_reason: EvolutionTrigger, rankings: List[Dict], 
                           survivors: List[Dict], new_bots: List[Dict]):
        """Registra evento de evolução no banco"""
        try:
            survivor_names = [s["name"] for s in survivors]
            replaced_names = [nb["replaced"] for nb in new_bots]
            new_bot_names = [nb["evolved_bot"].name for nb in new_bots]
            
            db.log_evolution(
                cycle_number=int(time.time()),  # Usa timestamp como ID
                survivor_names=survivor_names,
                replaced_names=replaced_names,
                new_bot_names=new_bot_names,
                rankings=rankings,
                trigger_reason=trigger_reason.value
            )
            
        except Exception as e:
            logger.error(f"Erro ao registrar evolução: {e}")
    
    def force_evolution(self) -> bool:
        """Força evolução manual (bypassa cooldown)"""
        if self.evolution_in_progress:
            logger.warning("Evolução já em progresso")
            return False
        
        logger.info("🚨 Forçando evolução manual")
        thread = threading.Thread(target=self._trigger_evolution, args=(EvolutionTrigger.MANUAL,))
        thread.daemon = True
        thread.start()
        return True
    
    def get_status(self) -> Dict:
        """Retorna status completo do sistema"""
        metrics = self.get_metrics()
        
        time_since_seconds = int(metrics.time_since_last_evolution.total_seconds())
        remaining_cooldown = max(0, int(self.cooldown_hours * 3600 - time_since_seconds)) if metrics.cooldown_active else 0
        trades_to_evolution = max(0, int(self.target_trades - metrics.global_trade_count))
        safety_net_trigger = (time_since_seconds >= self.max_time_without_evolution) and (not metrics.cooldown_active)
        trade_threshold_trigger = metrics.global_trade_count >= self.target_trades
        
        return {
            "global_trade_count": metrics.global_trade_count,
            "target_trades": self.target_trades,
            "progress_percent": (metrics.global_trade_count / self.target_trades) * 100,
            "last_evolution_time": metrics.last_evolution_time.isoformat(),
            "time_since_last_evolution": str(metrics.time_since_last_evolution),
            "hours_since_last_evolution": time_since_seconds / 3600.0,
            "cooldown_active": metrics.cooldown_active,
            "remaining_cooldown": remaining_cooldown,
            "trades_to_evolution": trades_to_evolution,
            "can_evolve": metrics.can_evolve,
            "trigger_reason": metrics.trigger_reason.value if metrics.trigger_reason else None,
            "triggers": {
                "trade_threshold": trade_threshold_trigger,
                "safety_net": safety_net_trigger
            },
            "evolution_in_progress": self.evolution_in_progress,
            "next_evolution_time": (metrics.last_evolution_time + timedelta(hours=self.cooldown_hours)).isoformat() if metrics.cooldown_active else None
        }
