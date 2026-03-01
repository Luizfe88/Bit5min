"""Telegram notification module for Polymarket Bot Arena."""

import requests
import json
import logging
from datetime import datetime
import pytz
from typing import Optional, Dict, Any, Callable

# Configurar logger para este módulo
logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Telegram bot notifier for sending messages and alerts."""
    
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        """Initialize Telegram notifier.
        
        Args:
            bot_token: Telegram bot token from BotFather
            chat_id: Chat ID where messages will be sent
            enabled: Whether notifications are enabled
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.brt_tz = pytz.timezone('America/Sao_Paulo')
        self.command_handler: Optional[Callable] = None
        
    def set_command_handler(self, handler: Callable[[str, str], str]):
        """Set command handler function.
        
        Args:
            handler: Function that receives (command, user_id) and returns response
        """
        self.command_handler = handler
    
    def get_updates(self, offset: int = 0, timeout: int = 30) -> list:
        """Get updates from Telegram API.
        
        Args:
            offset: Update ID offset for getting new updates
            timeout: Long polling timeout
            
        Returns:
            List of updates
        """
        if not self.enabled:
            return []
            
        try:
            url = f"{self.base_url}/getUpdates"
            payload = {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message"]
            }
            
            response = requests.post(url, json=payload, timeout=timeout + 10)
            response.raise_for_status()
            
            result = response.json()
            if result.get("ok"):
                return result.get("result", [])
            else:
                logger.error(f"Telegram API error: {result}")
                return []
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get Telegram updates: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting Telegram updates: {e}")
            return []
    
    def process_message(self, message: dict) -> bool:
        """Process an incoming message.
        
        Args:
            message: Telegram message object
            
        Returns:
            True if message was processed successfully
        """
        try:
            # Check if it's a text message
            if not message.get("text"):
                return False
                
            text = message["text"].strip()
            user_id = str(message.get("from", {}).get("id", ""))
            
            # Only process commands (start with /)
            if not text.startswith("/"):
                return False
                
            logger.info(f"Processing command: {text} from user {user_id}")
            
            # Process command if handler is set
            if self.command_handler:
                response = self.command_handler(text, user_id)
                if response:
                    return self.send_message(response)
            else:
                logger.warning("No command handler set")
                
            return True
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return False
        
    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message to Telegram.
        
        Args:
            message: Message text to send
            parse_mode: Parse mode for formatting (HTML, Markdown, etc.)
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.enabled:
            return False
            
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Telegram message sent successfully")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            return False
    
    def format_bot_status_message(self, bot_name: str, status: str, details: Dict[str, Any]) -> str:
        """Format a bot status message for Telegram.
        
        Args:
            bot_name: Name of the bot
            status: Status type (paused, resumed, error, etc.)
            details: Additional details about the status
            
        Returns:
            Formatted message for Telegram
        """
        brt_time = datetime.now(self.brt_tz).strftime("%d/%m/%Y %H:%M:%S")
        
        if status == "paused":
            reason = details.get("reason", "unknown")
            loss_amount = details.get("loss_amount", 0)
            max_loss = details.get("max_loss", 0)
            
            if reason == "daily_loss_limit":
                emoji = "🔴"
                title = "Bot Pausado - Limite Diário Atingido"
                message = f"{emoji} <b>{title}</b>\n\n"
                message += f"🤖 <b>Bot:</b> {bot_name}\n"
                message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
                message += f"💰 <b>Perda Atual:</b> ${loss_amount:.2f}\n"
                message += f"🚫 <b>Limite Máximo:</b> ${max_loss:.2f}\n"
                message += f"⚠️ <b>Motivo:</b> Limite diário de perdas atingido"
                
            elif reason == "consecutive_losses":
                emoji = "🟡"
                title = "Bot Pausado - Perdas Consecutivas"
                consecutive_count = details.get("consecutive_count", 0)
                
                message = f"{emoji} <b>{title}</b>\n\n"
                message += f"🤖 <b>Bot:</b> {bot_name}\n"
                message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
                message += f"📊 <b>Perdas Consecutivas:</b> {consecutive_count}\n"
                message += f"⚠️ <b>Motivo:</b> Detectadas {consecutive_count} perdas consecutivas"
                
            else:
                emoji = "⚠️"
                title = "Bot Pausado"
                message = f"{emoji} <b>{title}</b>\n\n"
                message += f"🤖 <b>Bot:</b> {bot_name}\n"
                message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
                message += f"📝 <b>Motivo:</b> {reason}"
                
        elif status == "resumed":
            emoji = "🟢"
            title = "Bot Retomado"
            message = f"{emoji} <b>{title}</b>\n\n"
            message += f"🤖 <b>Bot:</b> {bot_name}\n"
            message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
            message += f"✅ <b>Status:</b> Bot retomado com sucesso"
            
        elif status == "trade_executed":
            emoji = "💹"
            title = "Trade Executado"
            amount = details.get("amount", 0)
            side = details.get("side", "unknown")
            market = details.get("market", "unknown")
            
            trade_id = details.get("trade_id", "unknown")
            
            message = f"{emoji} <b>{title}</b>\n\n"
            message += f"🆔 <b>ID:</b> <code>{trade_id}</code>\n"
            message += f"🤖 <b>Bot:</b> {bot_name}\n"
            message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
            message += f"💰 <b>Valor:</b> ${amount:.2f}\n"
            message += f"📈 <b>Lado:</b> {side.upper()}\n"
            message += f"📊 <b>Mercado:</b> {market}"
            
        elif status == "error":
            emoji = "❌"
            title = "Erro no Bot"
            error_msg = details.get("error", "Erro desconhecido")
            
            message = f"{emoji} <b>{title}</b>\n\n"
            message += f"🤖 <b>Bot:</b> {bot_name}\n"
            message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
            message += f"📝 <b>Erro:</b> {error_msg}"
            
        else:
            emoji = "ℹ️"
            title = f"Status do Bot: {status.title()}"
            message = f"{emoji} <b>{title}</b>\n\n"
            message += f"🤖 <b>Bot:</b> {bot_name}\n"
            message += f"📅 <b>Data/Hora:</b> {brt_time}\n"
            if details:
                message += f"📝 <b>Detalhes:</b> {json.dumps(details, ensure_ascii=False)}"
                
        return message
    
    def notify_bot_paused(self, bot_name: str, reason: str, **kwargs) -> bool:
        """Send notification when a bot is paused.
        
        Args:
            bot_name: Name of the bot
            reason: Reason for pausing
            **kwargs: Additional details (loss_amount, max_loss, consecutive_count, etc.)
            
        Returns:
            True if message was sent successfully
        """
        details = kwargs.copy()
        details["reason"] = reason
        message = self.format_bot_status_message(bot_name, "paused", details)
        return self.send_message(message)
    
    def notify_bot_resumed(self, bot_name: str) -> bool:
        """Send notification when a bot is resumed.
        
        Args:
            bot_name: Name of the bot
            
        Returns:
            True if message was sent successfully
        """
        message = self.format_bot_status_message(bot_name, "resumed", {})
        return self.send_message(message)
    
    def notify_trade_executed(self, bot_name: str, amount: float, side: str, market: str, trade_id: str = "unknown") -> bool:
        """Send notification when a trade is executed.
        
        Args:
            bot_name: Name of the bot
            amount: Trade amount
            side: Trade side (yes/no)
            market: Market name
            trade_id: Optional trade ID
            
        Returns:
            True if message was sent successfully
        """
        details = {
            "amount": amount,
            "side": side,
            "market": market,
            "trade_id": trade_id
        }
        message = self.format_bot_status_message(bot_name, "trade_executed", details)
        return self.send_message(message)
    
    def notify_error(self, bot_name: str, error: str) -> bool:
        """Send notification when an error occurs.
        
        Args:
            bot_name: Name of the bot
            error: Error message
            
        Returns:
            True if message was sent successfully
        """
        details = {"error": error}
        message = self.format_bot_status_message(bot_name, "error", details)
        return self.send_message(message)


# Singleton instance
_telegram_notifier = None

def get_telegram_notifier() -> Optional[TelegramNotifier]:
    """Get the singleton Telegram notifier instance.
    
    Returns:
        TelegramNotifier instance or None if not configured
    """
    global _telegram_notifier
    
    if _telegram_notifier is None:
        try:
            import config
            if hasattr(config, 'TELEGRAM_BOT_TOKEN') and hasattr(config, 'TELEGRAM_CHAT_ID'):
                bot_token = config.TELEGRAM_BOT_TOKEN
                chat_id = config.TELEGRAM_CHAT_ID
                enabled = getattr(config, 'TELEGRAM_ENABLED', True)
                
                if bot_token and chat_id:
                    _telegram_notifier = TelegramNotifier(bot_token, chat_id, enabled)
                    logger.info("Telegram notifier initialized successfully")
                else:
                    logger.warning("Telegram bot token or chat ID not configured")
            else:
                logger.info("Telegram configuration not found in config.py")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram notifier: {e}")
    
    return _telegram_notifier