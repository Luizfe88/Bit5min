"""
core/market_lock.py
Sistema de Lock Global para evitar que múltiplos bots ataquem o mesmo mercado simultaneamente.
Previne Rate Limits e sobreposição de estratégias.
"""
import time
import threading

class MarketLock:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(MarketLock, cls).__new__(cls)
                    cls._instance.locks = {} # {market_id: timestamp}
                    cls._instance.lock_duration = 130 # segundos (120s rate limit + 10s buffer)
        return cls._instance

    def is_locked(self, market_id: str) -> bool:
        """Verifica se o mercado está bloqueado."""
        if market_id not in self.locks:
            return False
        
        last_time = self.locks[market_id]
        if time.time() - last_time < self.lock_duration:
            return True
        
        # Limpeza preguiçosa se expirou
        del self.locks[market_id]
        return False

    def acquire_lock(self, market_id: str):
        """Adquire lock para o mercado."""
        self.locks[market_id] = time.time()

# Instância global
market_lock = MarketLock()
