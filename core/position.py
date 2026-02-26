from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class OpenPosition:
    market_id: str
    bot_name: str
    direction: str  # "YES" or "NO"
    entry_price: float
    size_usd: float
    entry_time: float  # timestamp
    sl_price: Optional[float] = None  # Preço do TOKEN (YES ou NO) que aciona SL
    tp_price: Optional[float] = None  # Preço do TOKEN (YES ou NO) que aciona TP
    confidence: float = 0.0
    trade_id: Optional[str] = None
    shares: float = 0.0  # Quantidade de shares compradas
    token_id: Optional[str] = None # Para facilitar venda

    def __post_init__(self):
        # Validação básica
        if self.entry_price <= 0:
            raise ValueError(f"Entry price must be positive, got {self.entry_price}")
        if self.size_usd <= 0:
            raise ValueError(f"Size USD must be positive, got {self.size_usd}")
