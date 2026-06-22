# -*- coding: utf-8 -*-
"""
===================================
数据缓存管理模块
===================================

参考 daily_stock_analysis 项目实现
用于缓存实时行情和K线数据，减少重复请求

特性：
1. TTL (Time To Live) 过期机制
2. LRU (Least Recently Used) 淘汰策略
3. 按数据类型分区管理
"""

import time
import logging
from typing import Dict, Any, Optional, List
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    data: Any
    timestamp: float
    ttl: float
    hit_count: int = 0
    
    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.timestamp > self.ttl
    
    def age(self) -> float:
        """返回缓存年龄（秒）"""
        return time.time() - self.timestamp


class DataCache:
    """
    数据缓存管理器
    
    特性：
    - TTL 过期机制
    - 最大容量限制
    - LRU 淘汰策略
    - 线程安全
    """
    
    def __init__(
        self,
        name: str = "default",
        default_ttl: float = 600.0,  # 默认10分钟
        max_size: int = 1000         # 最大缓存条目数
    ):
        self.name = name
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存数据
        
        Returns:
            缓存的数据，不存在或过期返回 None
        """
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            entry = self._cache[key]
            
            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                logger.debug(f"[缓存] {self.name}:{key} 已过期，删除")
                return None
            
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._hits += 1
            
            logger.debug(f"[缓存命中] {self.name}:{key} (年龄: {entry.age():.0f}s/{entry.ttl:.0f}s)")
            return entry.data
    
    def set(
        self,
        key: str,
        data: Any,
        ttl: Optional[float] = None
    ) -> None:
        """
        设置缓存数据
        
        Args:
            key: 缓存键
            data: 缓存数据
            ttl: 过期时间（秒），None 使用默认值
        """
        with self._lock:
            while len(self._cache) >= self.max_size:
                oldest_key, _ = self._cache.popitem(last=False)
                logger.debug(f"[缓存] {self.name} 容量已满，淘汰: {oldest_key}")
            
            actual_ttl = ttl if ttl is not None else self.default_ttl
            self._cache[key] = CacheEntry(
                data=data,
                timestamp=time.time(),
                ttl=actual_ttl
            )
            
            logger.debug(f"[缓存更新] {self.name}:{key} TTL={actual_ttl}s")
    
    def delete(self, key: str) -> bool:
        """删除缓存条目"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"[缓存] {self.name}:{key} 已删除")
                return True
            return False
    
    def clear(self) -> int:
        """清空缓存"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"[缓存] {self.name} 已清空 {count} 条记录")
            return count
    
    def cleanup_expired(self) -> int:
        """清理过期条目"""
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
            
            if expired_keys:
                logger.debug(f"[缓存] {self.name} 清理 {len(expired_keys)} 条过期记录")
            return len(expired_keys)
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0
            
            return {
                'name': self.name,
                'size': len(self._cache),
                'max_size': self.max_size,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': f"{hit_rate:.1%}",
                'default_ttl': self.default_ttl
            }


# ============================================
# ============================================

_realtime_cache = DataCache(
    name="realtime",
    default_ttl=1200.0,  # 20分钟
    max_size=6000
)

_kline_cache = DataCache(
    name="kline",
    default_ttl=300.0,   # 5分钟
    max_size=500         # 最多500个交易对
)

_stock_info_cache = DataCache(
    name="stock_info",
    default_ttl=86400.0,  # 24小时
    max_size=6000
)


def get_realtime_cache() -> DataCache:
    """获取实时行情缓存"""
    return _realtime_cache


def get_kline_cache() -> DataCache:
    """获取K线数据缓存"""
    return _kline_cache


def get_stock_info_cache() -> DataCache:
    """获取股票信息缓存"""
    return _stock_info_cache


def generate_kline_cache_key(
    symbol: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None
) -> str:
    """
    生成K线缓存键
    
    格式: symbol:timeframe:limit[:before_time]
    """
    key = f"{symbol}:{timeframe}:{limit}"
    if before_time:
        key += f":{before_time}"
    return key
