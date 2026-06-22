"""
数据库和缓存配置
"""
import os

class MetaRedisConfig(type):
    """Redis 配置"""
    
    @property
    def HOST(cls):
        return os.getenv('REDIS_HOST', 'localhost')
    
    @property
    def PORT(cls):
        return int(os.getenv('REDIS_PORT', 6379))
    
    @property
    def PASSWORD(cls):
        return os.getenv('REDIS_PASSWORD', None)
    
    @property
    def DB(cls):
        return int(os.getenv('REDIS_DB', 0))
    
    @property
    def CONNECT_TIMEOUT(cls):
        return int(os.getenv('REDIS_CONNECT_TIMEOUT', 5))
    
    @property
    def SOCKET_TIMEOUT(cls):
        return int(os.getenv('REDIS_SOCKET_TIMEOUT', 5))
    
    @property
    def MAX_CONNECTIONS(cls):
        return int(os.getenv('REDIS_MAX_CONNECTIONS', 10))


class RedisConfig(metaclass=MetaRedisConfig):
    """Redis 缓存配置"""
    
    @classmethod
    def get_url(cls) -> str:
        """获取 Redis 连接 URL"""
        if cls.PASSWORD:
            return f"redis://:{cls.PASSWORD}@{cls.HOST}:{cls.PORT}/{cls.DB}"
        return f"redis://{cls.HOST}:{cls.PORT}/{cls.DB}"


class MetaCacheConfig(type):
    """缓存业务配置"""
    
    @property
    def ENABLED(cls):
        return os.getenv('CACHE_ENABLED', 'False').lower() == 'true'

    @property
    def DEFAULT_EXPIRE(cls):
        return int(os.getenv('CACHE_EXPIRE', 300))

    @property
    def KLINE_CACHE_TTL(cls):
        return {
            '1m': 5,       # 1分钟K线缓存5秒
            '3m': 30,      # 3分钟K线缓存30秒
            '5m': 60,      # 5分钟K线缓存1分钟
            '15m': 300,    # 15分钟K线缓存5分钟
            '30m': 300,    # 30分钟K线缓存5分钟
            '1H': 300,     # 1小时K线缓存5分钟
            '4H': 300,     # 4小时K线缓存5分钟
            '1D': 300,     # 日K线缓存5分钟
            '1h': 300,
            '4h': 300,
            '1d': 300,
        }

    @property
    def ANALYSIS_CACHE_TTL(cls):
        return 3600

    @property
    def PRICE_CACHE_TTL(cls):
        return 10


class CacheConfig(metaclass=MetaCacheConfig):
    """缓存配置"""
    pass
