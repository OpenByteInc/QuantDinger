"""
Logging utilities (local-only friendly).
"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger():
    """配置全局日志"""
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format
    )
    
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.WARNING)
    
    kline_logger = logging.getLogger('app.routes.kline')
    kline_logger.setLevel(logging.WARNING)

    _usdt = logging.getLogger("app.services.usdt_payment_service")
    _usdt.setLevel(logging.INFO)
    _billing = logging.getLogger("app.routes.billing")
    _billing.setLevel(logging.INFO)
    
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        Logger 实例
    """
    return logging.getLogger(name)

