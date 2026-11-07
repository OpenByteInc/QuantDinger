"""Read-only public OHLCV continuity probe; never creates an app or trading session."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_sources.crypto import CryptoDataSource
from app.data_sources.native_crypto import NativeCryptoPublicClient, _TIMEFRAME_SECONDS
from app.config.data_sources import CryptoPublicConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exchanges', default='gate,okx,bybit,bitget,binance,htx')
    parser.add_argument('--timeframes', default='1m,15m,1h,4h,1d,1w')
    parser.add_argument('--markets', default='spot,swap')
    parser.add_argument('--mode', choices=['native', 'source', 'full'], default='native')
    parser.add_argument('--pages', type=int, default=1)
    args = parser.parse_args()
    for venue in args.exchanges.split(','):
        for market in args.markets.split(','):
            client = NativeCryptoPublicClient(
                venue, market, timeout_ms=7000, proxy=CryptoPublicConfig.PROXY,
                okx_host=os.getenv('OKX_API_HOST', 'www.okx.com'),
            )
            for tf in args.timeframes.split(','):
                result = dict(venue=venue, market=market, timeframe=tf, mode=args.mode)
                try:
                    step = _TIMEFRAME_SECONDS[tf] * 1000
                    source = object.__new__(CryptoDataSource)
                    source.exchange = client
                    source._allow_public_fallback = False
                    source._preferred_public_exchange_id = ''
                    source._scoped_market_type = market
                    source._symbol_for_scoped_market = lambda _symbol: 'BTC/USDT'
                    source._is_invalid_symbol_cached = lambda _symbol: False
                    source.log_result = lambda *_args: None
                    qd_tf = CryptoDataSource._exchange_to_qd_timeframe(tf, tf)
                    def as_ohlcv(rows):
                        return [[row['time'] * 1000, row['open'], row['high'], row['low'], row['close'], row['volume']] for row in rows]
                    latest = (as_ohlcv(source.get_kline('BTC/USDT', qd_tf, 480))
                              if args.mode == 'full' else client.fetch_ohlcv('BTC/USDT', tf, limit=480))
                    if not latest:
                        raise ValueError('No latest candles')
                    boundary = latest[0][0]
                    if args.mode == 'native':
                        start = boundary - step * 1000
                        history = client.fetch_ohlcv('BTC/USDT', tf, since=start, limit=1000)
                        result['left_skip_bars'] = (history[0][0] - start) / step if history else None
                    elif args.mode == 'source':
                        history = source._fetch_ohlcv('BTC/USDT', tf, 500, boundary // 1000, qd_tf)
                    else:
                        history = as_ohlcv(source.get_kline('BTC/USDT', qd_tf, 500, boundary // 1000))
                    history = sorted({row[0]: row for row in history if row[0] < boundary}.values())
                    result.update(
                        latest_count=len(latest), history_count=len(history),
                        boundary=boundary, history_last=history[-1][0] if history else None,
                        missing_at_join=(boundary - history[-1][0]) / step - 1 if history else None,
                        internal_gaps=sum(b[0] - a[0] != step for a, b in zip(history, history[1:])),
                    )
                    if args.mode == 'full' and history:
                        extra = []
                        for page in range(1, args.pages):
                            cursor = history[0][0]
                            history = as_ohlcv(source.get_kline('BTC/USDT', qd_tf, 500, cursor // 1000))
                            extra.append(dict(page=page + 1, count=len(history),
                                              missing_at_join=(cursor - history[-1][0]) / step - 1 if history else None,
                                              internal_gaps=sum(b[0] - a[0] != step for a, b in zip(history, history[1:]))))
                            if not history:
                                break
                        result['older_pages'] = extra
                except Exception as exc:
                    result['error'] = str(exc)[:350]
                print(json.dumps(result), flush=True)
                time.sleep(0.1)


if __name__ == '__main__':
    main()
