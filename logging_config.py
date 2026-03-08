"""Custom logging configuration with BRT timezone support."""

import logging
import pytz
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

class BRTFormatter(logging.Formatter):
    """Custom formatter that uses BRT (Brasília) timezone."""
    
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)
        # BRT = UTC-3 (Brasília)
        self.brt_tz = pytz.timezone('America/Sao_Paulo')
    
    def formatTime(self, record, datefmt=None):
        """Format time in BRT timezone."""
        # Convert timestamp to BRT
        dt = datetime.fromtimestamp(record.created, self.brt_tz)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

def setup_logging_with_brt(name, level=logging.INFO, log_file=None):
    """Setup logging with BRT timezone.
    
    Args:
        name: Logger name
        level: Logging level
        log_file: Optional log file path
    
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # Create formatter with BRT timezone
    formatter = BRTFormatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    
    # Prevent propagation to root logger (avoids duplicates if root has handlers)
    logger.propagate = False
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        # Use TimedRotatingFileHandler for rotation every 3 hours
        file_handler = TimedRotatingFileHandler(
            log_file,
            when='H',           # Rotate by hours
            interval=3,         # Every 3 hours
            backupCount=168,    # Keep 1 week (24 * 7 hours)
            encoding='utf-8',
            delay=False,
            utc=False           # Use local time for rotation
        )
        
        # Suffix format: dd-mm-yy-HH.log
        # This will be appended to the base filename
        file_handler.suffix = "%d-%m-%y-%H.log"
        
        # Custom namer to clean up the filename
        # Default behavior: base.log -> base.log.dd-mm-yy-HH.log
        # Desired behavior: trading-arena.log -> trading-arena-dd-mm-yy-HH.log
        def custom_namer(name):
            # name comes in as /path/to/trading-arena.log.26-02-26-14.log
            # We want /path/to/trading-arena-26-02-26-14.log
            return name.replace(".log.", "-")
            
        file_handler.namer = custom_namer
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Prevent propagation to root logger to avoid duplication if root has handlers
    logger.propagate = False
    
    return logger