# -*- coding: utf-8 -*-
"""
===================================
防封禁工具模块 (Rate Limiter)
===================================

参考 daily_stock_analysis 项目实现
提供反爬虫策略：
1. 随机休眠（Jitter）
2. 随机 User-Agent 轮换
3. 指数退避重试
4. 请求频率限制
"""

import time
import random
import logging
from typing import Optional, Callable, Any, Type, Tuple
from functools import wraps

logger = logging.getLogger(__name__)


# ============================================
# ============================================

USER_AGENTS = [
    # Chrome Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    # Chrome Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    # Firefox
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    # Safari
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    # Edge
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    # Linux Chrome
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


def get_random_user_agent() -> str:
    """获取随机 User-Agent"""
    return random.choice(USER_AGENTS)


def get_request_headers(referer: Optional[str] = None) -> dict:
    """
    获取带有随机 User-Agent 的请求头
    
    Args:
        referer: 可选的 Referer 头
        
    Returns:
        请求头字典
    """
    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    if referer:
        headers['Referer'] = referer
    
    return headers


# ============================================
# ============================================

def random_sleep(
    min_seconds: float = 1.0,
    max_seconds: float = 3.0,
    log: bool = False
) -> None:
    """
    随机休眠（Jitter）
    
    防封禁策略：模拟人类行为的随机延迟
    在请求之间加入不规则的等待时间
    
    Args:
        min_seconds: 最小休眠时间（秒）
        max_seconds: 最大休眠时间（秒）
        log: 是否记录日志
    """
    sleep_time = random.uniform(min_seconds, max_seconds)
    if log:
        logger.debug(f"随机休眠 {sleep_time:.2f} 秒...")
    time.sleep(sleep_time)


# ============================================
# ============================================

class RateLimiter:
    """
    请求频率限制器
    
    确保请求之间有最小间隔时间
    """
    
    def __init__(
        self,
        min_interval: float = 1.0,
        jitter_min: float = 0.5,
        jitter_max: float = 1.5
    ):
        """
        初始化频率限制器
        
        Args:
            min_interval: 最小请求间隔（秒）
            jitter_min: 随机抖动最小值（秒）
            jitter_max: 随机抖动最大值（秒）
        """
        self.min_interval = min_interval
        self.jitter_min = jitter_min
        self.jitter_max = jitter_max
        self._last_request_time: Optional[float] = None
    
    def wait(self) -> float:
        """
        等待直到可以发起下一次请求
        
        Returns:
            实际等待的时间（秒）
        """
        wait_time = 0.0
        
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                time.sleep(wait_time)
        
        jitter = random.uniform(self.jitter_min, self.jitter_max)
        time.sleep(jitter)
        wait_time += jitter
        
        self._last_request_time = time.time()
        
        return wait_time
    
    def reset(self) -> None:
        """重置限制器"""
        self._last_request_time = None


# ============================================
# ============================================

def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    指数退避重试装饰器
    
    Args:
        max_attempts: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        exponential_base: 指数基数
        exceptions: 需要重试的异常类型
        on_retry: 重试时的回调函数
        
    使用示例:
        @retry_with_backoff(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        def fetch_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        logger.error(f"[重试] {func.__name__} 已达最大重试次数 ({max_attempts})，放弃")
                        raise
                    
                    delay = min(
                        base_delay * (exponential_base ** (attempt - 1)),
                        max_delay
                    )
                    delay *= random.uniform(0.8, 1.2)
                    
                    logger.warning(
                        f"[重试] {func.__name__} 第 {attempt}/{max_attempts} 次失败: {e}, "
                        f"等待 {delay:.1f}s 后重试..."
                    )
                    
                    if on_retry:
                        on_retry(attempt, e)
                    
                    time.sleep(delay)
            
            raise last_exception
        
        return wrapper
    return decorator


# ============================================
# ============================================

_eastmoney_limiter = RateLimiter(
    min_interval=2.0,
    jitter_min=1.0,
    jitter_max=3.0
)

_tencent_limiter = RateLimiter(
    min_interval=1.0,
    jitter_min=0.5,
    jitter_max=1.5
)

_akshare_limiter = RateLimiter(
    min_interval=2.0,
    jitter_min=1.5,
    jitter_max=3.5
)


def get_eastmoney_limiter() -> RateLimiter:
    """获取东方财富限流器"""
    return _eastmoney_limiter


def get_tencent_limiter() -> RateLimiter:
    """获取腾讯财经限流器"""
    return _tencent_limiter


def get_akshare_limiter() -> RateLimiter:
    """获取 Akshare 限流器"""
    return _akshare_limiter
