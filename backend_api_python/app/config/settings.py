"""
应用主配置
"""
import os

class MetaConfig(type):
    
    @property
    def HOST(cls):
        return os.getenv('PYTHON_API_HOST', '0.0.0.0')

    @property
    def PORT(cls):
        return int(os.getenv('PYTHON_API_PORT', 5000))

    @property
    def DEBUG(cls):
        return os.getenv('PYTHON_API_DEBUG', 'False').lower() == 'true'

    @property
    def APP_NAME(cls):
        return 'QuantDinger Python API'

    @property
    def VERSION(cls):
        return '2.0.0'

    @property
    def SECRET_KEY(cls):
        return os.getenv('SECRET_KEY', 'quantdinger-secret-key-change-me')

    @property
    def ADMIN_USER(cls):
        return os.getenv('ADMIN_USER', 'quantdinger')

    @property
    def ADMIN_PASSWORD(cls):
        return os.getenv('ADMIN_PASSWORD', '123456')

    
    @property
    def LOG_LEVEL(cls):
        return os.getenv('LOG_LEVEL', 'INFO')

    @property
    def LOG_DIR(cls):
        return os.getenv('LOG_DIR', 'logs')

    @property
    def LOG_FILE(cls):
        return os.getenv('LOG_FILE', 'app.log')

    @property
    def LOG_MAX_BYTES(cls):
        return int(os.getenv('LOG_MAX_BYTES', 10 * 1024 * 1024))

    @property
    def LOG_BACKUP_COUNT(cls):
        return int(os.getenv('LOG_BACKUP_COUNT', 5))


    @property
    def RATE_LIMIT(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('app', {}).get('rate_limit')
        return int(val) if val is not None else int(os.getenv('RATE_LIMIT', 100))


    @property
    def ENABLE_CACHE(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('app', {}).get('enable_cache')
        if val is not None:
            return bool(val)
        return os.getenv('ENABLE_CACHE', 'False').lower() == 'true'

    @property
    def ENABLE_REQUEST_LOG(cls):
        from app.utils.config_loader import load_addon_config
        val = load_addon_config().get('app', {}).get('enable_request_log')
        if val is not None:
            return bool(val)
        return os.getenv('ENABLE_REQUEST_LOG', 'True').lower() == 'true'

class Config(metaclass=MetaConfig):
    """应用配置类"""
    
    @classmethod
    def get_log_path(cls) -> str:
        """获取日志文件完整路径"""
        return os.path.join(cls.LOG_DIR, cls.LOG_FILE)
