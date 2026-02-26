"""
Integração do BotEvolutionManager com o sistema existente
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime

import db
from bot_evolution_manager import BotEvolutionManager
from bots.base_bot import BaseBot

logger = logging.getLogger(__name__)


class EvolutionIntegration:
    """Integra o novo sistema de evolução com o sistema existente"""
    
    def __init__(self):
        self._active_bots = []
        # Cria evolution manager com função para obter bots ativos
        self.evolution_manager = BotEvolutionManager(bots_source=self.get_active_bots_for_evolution)
        logger.info("🔄 EvolutionIntegration iniciado")
    
    def on_trade_resolved(self, bot_name: str, trade_data: Dict):
        """
        Chamado quando um trade é resolvido
        
        Args:
            bot_name: Nome do bot
            trade_data: Dados do trade resolvido
        """
        try:
            # Extrai informações do trade
            trade_result = {
                'market_id': trade_data.get('market_id'),
                'outcome': trade_data.get('outcome'),
                'pnl': trade_data.get('pnl', 0),
                'resolved_at': datetime.now().isoformat()
            }
            
            # Notifica evolution manager
            self.evolution_manager.increment_trade_counter(bot_name, trade_result)
            
        except Exception as e:
            logger.error(f"Erro ao processar trade resolvido: {e}", exc_info=True)
    
    def set_active_bots(self, bots: List[BaseBot]):
        """
        Define os bots ativos para evolução
        
        Args:
            bots: Lista de bots ativos
        """
        # Only log if list actually changed to avoid spam
        current_names = sorted([b.name for b in bots])
        cached_names = sorted([b.name for b in self._active_bots])
        
        self._active_bots = bots
        
        if current_names != cached_names:
            logger.info(f"🤖 Bots ativos definidos: {current_names if bots else 'NENHUM'}")
            logger.info(f"📊 Total de bots ativos: {len(bots)}")
    
    def get_active_bots_for_evolution(self) -> List[BaseBot]:
        """
        Obtém bots ativos para evolução
        """
        return self._active_bots
        # Por enquanto, retorna lista vazia - será integrado com arena.py
        return []
    
    def update_arena_with_evolved_bots(self, survivors: List[Dict], new_bots: List[Dict]):
        """
        Atualiza arena.py com novos bots evoluídos
        
        Args:
            survivors: Lista de bots sobreviventes
            new_bots: Lista de novos bots evoluídos
        """
        try:
            # Esta função será chamada pelo evolution manager
            # Aqui devemos atualizar a lista de bots ativa na arena
            
            survivor_names = [s['name'] for s in survivors]
            new_bot_names = [nb['evolved_bot'].name for nb in new_bots]
            
            logger.info(f"🏆 Sobreviventes: {survivor_names}")
            logger.info(f"⭐ Novos bots: {new_bot_names}")
            
            # A integração específica depende de como arena.py gerencia os bots
            # Por enquanto, logamos as mudanças
            
        except Exception as e:
            logger.error(f"Erro ao atualizar arena: {e}", exc_info=True)
    
    def get_evolution_status(self) -> Dict:
        """Retorna status completo do sistema de evolução"""
        return self.evolution_manager.get_status()
    
    def force_evolution(self) -> bool:
        """Força uma evolução manual"""
        return self.evolution_manager.force_evolution()
    
    def check_and_trigger_evolution_if_needed(self):
        """Verifica e dispara a evolução se os critérios forem atendidos."""
        try:
            self.evolution_manager.check_evolution_triggers()
        except Exception as e:
            logger.error(f"Erro ao verificar gatilhos de evolução: {e}", exc_info=True)
    
    def should_run_regular_evolution(self) -> bool:
        """
        Verifica se deve executar evolução regular (8h safety net) ou apenas evolução por trades
        
        Returns:
            True se deve executar evolução regular (safety net), False se usar evolução por trades
        """
        status = self.get_evolution_status()
        
        # Se já atingiu o gatilho de trades, usa evolução por trades
        if status['trades_to_evolution'] <= 0:
            logger.info("🎯 Gatilho de trades atingido - usar evolução por trades")
            return False
        
        # Se estiver próximo do gatilho de trades (90%+), espera mais um pouco
        if status['progress_percent'] > 90:
            logger.info(f"📈 Próximo do gatilho ({status['progress_percent']:.1f}%) - aguardando trades")
            return False
        
        # Se já estiver em cooldown, não evolui por tempo
        if status['cooldown_active']:
            logger.info("⏰ Em cooldown - aguardando")
            return False
        
        # Só usa evolução por tempo se estiver próximo do safety net (7h+ sem evolução)
        hours_since_evolution = status.get('hours_since_last_evolution', 0)
        if hours_since_evolution >= 7:  # 7h de 8h máximo
            logger.info(f"⚠️ Safety net ativado: {hours_since_evolution:.1f}h sem evolução")
            return True
        
        # Padrão: aguardar por trades
        logger.info(f"🕐 Aguardando trades ({status['trades_to_evolution']} faltando) ou safety net")
        return False


# Singleton global
evolution_integration = EvolutionIntegration()


def on_trade_resolved(bot_name: str, trade_data: Dict):
    """Função global para notificar resolução de trade"""
    evolution_integration.on_trade_resolved(bot_name, trade_data)


def get_evolution_status() -> Dict:
    """Função global para obter status da evolução"""
    return evolution_integration.get_evolution_status()


def force_evolution() -> bool:
    """Função global para forçar evolução"""
    return evolution_integration.force_evolution()