import logging
import psutil
import os

logger = logging.getLogger(__name__)

def watchdog_check():
    """Auto-verificação de saúde do bot"""
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent

    if cpu > 90 or mem > 90:
        logger.warning("⚠️ Uso de recursos alto. Considere reiniciar.")
    else:
        logger.info("✅ Sistema saudável.")
