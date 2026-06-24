"""
HTTP 工具模块
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_retry_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (500, 502, 503, 504)
) -> requests.Session:
    """
    获取带重试机制的 HTTP Session
    
    Args:
        retries: 重试次数
        backoff_factor: 重试间隔因子
        status_forcelist: 需要重试的 HTTP 状态码
        
    Returns:
        配置好的 Session 实例
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


global_session = get_retry_session()

