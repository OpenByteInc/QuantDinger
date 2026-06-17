# KTX 交易所接入文档

> 整理时间：2026-05-27 | 最后更新：2026-05-27（新增合约lpc交易要点）
> 代码位置：`backend_api_python/app/services/live_trading/ktx.py`
> 测试目录：`backend_api_python/tests/ktx-case/`

---

## 1. 概述

KTX 是统一账户模式的加密货币交易所（现货 + U本位合约），**不支持 CCXT**，需使用原生 REST API 接入。

**API 域名：**
- 市场数据：`https://api.ktx.com/api`
- 用户私有数据：`https://api.ktx.com/papi`

**认证方式：**
- 签名：`HMAC-SHA256(apiSecret, expireTime + queryString | body)`
- Headers: `api-key`, `api-sign`, `api-expire-time`

---

## 2. 账户体系

KTX 采用统一账户模式，资产分为两层：

### 2.1 交易账户（现货 + 合约都在这里）

**端点：** `GET /papi/v1/trade/accounts`

现货资产（买入的币）和合约保证金共用此账户。

**API 方法：**
```python
get_trade_balance(asset?)      # 获取交易账户资产
get_spot_balance(asset?)      # get_trade_balance() 的别名
```

**返回字段语义（交易账户）：**

| 字段 | 语义 |
|------|------|
| `balance` | 总资产 |
| `free` | 账户总可用（等于 balance） |
| `withdrawable` | **实际可提取/可用的部分**（扣除挂单冻结后的余额） |
| `locked` | **挂单等冻结不可用部分** |
| `collateral` | 是否作为保证金币种（USDT=true，BTC/ETH=false） |

> 示例：USDT `balance=30.435, withdrawable=4.675, locked=0`，说明 25.76 USDT 被其它持仓的保证金占用，只有 4.67 可自由支配。

### 2.2 钱包账户（独立于交易账户）

**端点：** `POST /papi/v1/main/accounts`

存放未划入交易账户的资产（充币到账后默认在这里）。

**API 方法：**
```python
get_wallet_balance(asset?)    # 获取钱包账户资产
get_account()                  # get_wallet_balance() 别名
get_balance()                 # get_account() 别名
```

### 2.3 资产划转

**端点：** `POST /papi/v1/transfer`

```python
spot_transfer(symbol, amount, direction)
# direction: "WALLET_TRADE"  = 钱包 → 交易账户
#            "TRADE_WALLET"   = 交易账户 → 钱包
```

---

## 3. market 参数规范

KTX 所有订单类 API **必须显式传递 market 参数**，否则返回 404：

| market 值 | 说明 |
|-----------|------|
| `spot` | 现货 |
| `lpc` | U本位永续合约 |

```python
_ktx_market_param(market="")  # 内部方法
# 若传入空字符串，则自动使用 self.market_type（"spot" 或 "lpc"）
```

---

## 4. 账户相关 API

| 方法 | 端点 | 账户类型 | 说明 |
|------|------|----------|------|
| `get_wallet_balance(asset?)` | POST `/v1/main/accounts` | 钱包 | 未划转资产 |
| `get_account()` | POST `/v1/main/accounts` | 钱包 | get_wallet_balance 别名 |
| `get_trade_balance(asset?)` | GET `/v1/trade/accounts` | 交易 | 现货+合约总资产 |
| `get_spot_balance(asset?)` | GET `/v1/trade/accounts` | 交易 | get_trade_balance 别名 |
| `spot_transfer(symbol, amount, direction)` | POST `/v1/transfer` | 划转 | 钱包↔交易账户 |
| `get_ledger(...)` | GET `/v1/ledgers` | 账单 | 划转/交易/手续费/资金费 |
| `get_positions(position_id?, market?, symbol?)` | GET `/v1/positions` | 合约 | 过滤 quantity=0 空仓位 |

### 4.1 获取账单

```python
get_ledger(asset?, start_time?, end_time?, ledger_type?, limit?)
# ledger_type: "transfer" | "trade" | "fee" | "rebate" | "funding"
```

---

## 5. 订单相关 API

**重要：** 所有订单接口 `market` 参数必传。

| 方法 | 端点 | 说明 |
|------|------|------|
| `place_market_order(symbol, side, qty, reduce_only?, pos_side?, client_order_id?)` | POST `/v1/order` | 市价下单 |
| `place_limit_order(symbol, side, qty, price, reduce_only?, pos_side?, time_in_force?, client_order_id?)` | POST `/v1/order` | 限价下单 |
| `get_order(symbol, order_id?, client_order_id?, market?)` | GET `/v1/order` (params: id=) | 订单详情 |
| `get_open_orders(symbol?, market?)` | GET `/v1/pending/orders` | 未完成订单 |
| `get_history_orders(symbol?, market?, start_time?, end_time?, limit?)` | GET `/v1/history/orders` | 历史订单（3个月内） |
| `cancel_order(symbol, order_id?, client_order_id?, market?)` | POST `/v1/order/delete` | 取消订单 |
| `wait_for_fill(symbol, order_id?, client_order_id?, max_wait_sec?, poll_interval_sec?)` | 轮询 get_order | 等待成交 |

### 5.1 设置杠杆

```python
set_leverage(symbol, leverage, position_id?)
# 端点：POST /v1/change/leverage
# position_id: 仓位ID，若为空则按 symbol 设置（best-effort）
```

---

## 6. 行情 API（公开，无需签名）

| 方法 | 端点 | 说明 |
|------|------|------|
| `get_ticker(symbol, market?)` | GET `/api/v1/ticker` | 最新价、成交量等 |
| `ping()` | GET `/api/v1/products` | 连通性检查 |

---

## 7. 已知问题与修复记录

### 7.1 仓位接口返回假仓位

**问题：** `/v1/positions` 不指定 market 时返回所有市场（含从未交易的空记录）。

**修复：** 始终强制设置 `market=lpc`，并过滤 `quantity=0` 的仓位记录。

### 7.2 POST 空 body 导致 Invalid JSON

**问题：** `get_spot_balance()` 等 POST 接口传 `None` body 时，签名用空字符串，导致 KTX 返回 `-12102 Invalid JSON`。

**修复：** POST 空 body 时发送 `{}` 而非空字符串。

### 7.3 订单端点路径错误

| 方法 | 错误路径 | 正确路径 |
|------|----------|----------|
| `get_order` | `/v1/orders/{id}` | `/v1/order?id={id}` |
| `cancel_order` | `DELETE /v1/orders/{id}` | `POST /v1/order/delete` |
| `get_open_orders` | `/v1/orders` | `/v1/pending/orders` |

### 7.4 set_leverage 端点错误

**错误：** `/v1/trade/leverage`
**正确：** `/v1/change/leverage`（需要 `positionId`）

### 7.5 下单字段名错误（state=-21108）

**问题：** 现货下单返回 `state=-21108`，无错误消息。

**根因：** KTX API 请求体字段名与代码中不一致。

| 字段 | 错误写法 | 正确写法 |
|------|----------|----------|
| 下单数量 | `amount` | `quantity` |
| 有效期 | `time_in_force` (大写GTC) | `timeInForce` (小写gtc) |
| 仓位合并 | 缺失 | `positionMerge: "none"` (现货必传) |
| 订单ID | `id` | `orderId` |

**修复：** `place_market_order` 和 `place_limit_order` 方法中：
- `amount` → `quantity`
- `time_in_force` → `timeInForce`，值改为小写
- 添加 `positionMerge: "none"`
- 提取订单ID使用 `orderId` 字段

### 7.6 GET 请求 query 参数签名不匹配

**问题：** `get_order()` 调用返回 `state=-12101 Invalid Signature`。

**根因：** 将 `?id=xxx` 直接拼在 URL 路径中，但签名计算只从 `params` 参数构建 query string。

**修复：**
```python
# 错误：签名不包含 id 参数
self._signed_request("GET", f"/v1/order?id={order_id}")

# 正确：id 通过 params 传入，参与签名计算
self._signed_request("GET", "/v1/order", params={"id": order_id})
```

### 7.7 产品精度字段名不匹配

**问题：** `_normalize_qty` / `_normalize_price` 使用 `amount_scale` / `price_scale` 等字段名，KTX 实际返回 `quantityScale` / `priceScale`。

**修复：** 使用 KTX 实际字段名：
- `quantityScale` (int) → 数量精度
- `quantityIncrement` (str) → 数量步进
- `priceScale` (int) → 价格精度
- `priceIncrement` (str) → 价格步进
- `minOrderSize` (str) → 最小下单数量
- `minOrderValue` (str) → 最小下单金额（KTX 实际为 1 USDT）

### 7.8 get_ticker 返回 result 为 dict 非 list

**问题：** 代码按 list 解析 `get_ticker` 的 `result` 字段，实际返回的是 dict。

**修复：** 判断 result 类型，dict 直接使用，list 取首个元素。

### 7.9 合约下单缺少 marginMethod 参数

**问题：** 合约下单返回 `state=-12013`，`"Missing parameter 'marginMethod'"`。

**根因：** 现货下单不需要 marginMethod，但合约下单必须传递。

**修复：** 在 `place_limit_order` 和 `place_market_order` 中，当 `market_type="swap"` 时自动添加 `marginMethod`，默认为 `"cross"`（全仓）。

### 7.10 合约平仓缺少 close 和 positionId 参数

**问题：** 平仓单被当作开仓单处理（`close=false`，`positionMerge` 按 side 推断为反方向）。

**根因：** 代码没有 `close` 和 `position_id` 参数，平仓时 positionMerge 由 side 自动推断（sell→short），但平多应保持 `positionMerge=long`。

**修复：** 新增 `close` 和 `position_id` 参数，平仓时通过 `pos_side` 显式指定 `positionMerge`：
```python
# 平多仓
client.place_limit_order(
    ..., side="sell", pos_side="long", close=True, position_id=str(pos_id),
)
```

### 7.11 minOrderSize 校验过严

**问题：** 0.002 ETH 合约下单被 `_normalize_qty` 拒绝，因 0.002 < minOrderSize(0.008)。

**根因：** `minOrderSize` 仅对 mini 合约生效，cross 模式下 0.002 ETH 可以正常下单。

**修复：** 放宽 `_normalize_qty` 校验，qty < minOrderSize 时仅打印警告不拒绝，由交易所做最终校验。

---

已通过真实 API 完成以下现货交易操作验证：

| 功能 | 方法 | 验证结果 |
|------|------|----------|
| 行情查询 | `get_ticker()` | ✅ BTC/USDT 最新价 73683 USDT |
| 余额查询 | `get_trade_balance()` | ✅ withdrawable=可用余额 |
| 限价买入 | `place_limit_order(side="buy")` | ✅ 0.00006 BTC @ 60000 |
| 限价卖出 | `place_limit_order(side="sell")` | ✅ 0.0001 BTC @ 高行情10% |
| 市价买入 | `place_market_order(side="buy")` | ✅ ~1.5U BTC，即时 filled |
| 市价卖出 | `place_market_order(side="sell")` | ✅ 0.00002 BTC，即时 filled |
| 查询挂单 | `get_open_orders()` | ✅ 支持 BTC/NANA 等多币种 |
| 查询订单 | `get_order()` | ✅ 签名正确 |
| 撤销挂单 | `cancel_order()` | ✅ status=cancelled |

**订单返回关键字段：**
- `orderId`：订单ID（不是 id）
- `status`：accepted=挂单中，filled=已成交，cancelled=已撤销
- `fills`：成交详情数组，含 price/quantity/fees
- `executedQty`：已成交数量
- `executedCost`：已成交金额

---

## 8.5 合约（lpc）交易要点

### 8.5.1 合约下单必传参数

合约下单相比现货，**必须额外传递**以下参数：

| 参数 | 类型 | 说明 | 取值 |
|------|------|------|------|
| `positionMerge` | string | 持仓方向 | `"long"` 开多/平多，`"short"` 开空/平空，`"none"` 分仓/mini |
| `marginMethod` | string | 保证金模式 | `"cross"` 全仓，`"isolate"` 逐仓 |
| `leverage` | int | 杠杆倍数 | 如 3, 5, 10, 20 |
| `close` | bool | 开/平仓标志 | `true` 平仓，`false` 开仓 |
| `positionId` | string | 仓位ID | 平仓时建议传入（从 get_positions 获取） |

**标准常量**（从 `ktx.py` 导入）：

```python
from app.services.live_trading.ktx import (
    POS_MERGE_LONG, POS_MERGE_SHORT, POS_MERGE_NONE,
    MARGIN_CROSS, MARGIN_ISOLATE,
    CLOSE_OPEN, CLOSE_CLOSE,
    MARKET_SPOT, MARKET_LPC,
)
```

| 常量 | 值 | 用途 |
|------|-----|------|
| `POS_MERGE_LONG` | `"long"` | 合并多仓：开多 / 平多 |
| `POS_MERGE_SHORT` | `"short"` | 合并空仓：开空 / 平空 |
| `POS_MERGE_NONE` | `"none"` | 分仓（现货默认 / mini合约） |
| `MARGIN_CROSS` | `"cross"` | 全仓模式 |
| `MARGIN_ISOLATE` | `"isolate"` | 逐仓模式 |
| `CLOSE_OPEN` | `False` | 开仓 |
| `CLOSE_CLOSE` | `True` | 平仓 |
| `MARKET_SPOT` | `"spot"` | 现货 |
| `MARKET_LPC` | `"lpc"` | U本位永续 |

### 8.5.2 多空双开（Hedge Mode）

KTX 支持同一交易对同时持有多仓和空仓，通过 `positionMerge` 区分：

| 操作 | positionMerge | side | close |
|------|---------------|------|-------|
| 开多 | `long` | `buy` | `false` |
| 开空 | `short` | `sell` | `false` |
| 平多 | `long` | `sell` | `true` |
| 平空 | `short` | `buy` | `true` |

> **关键：** 平仓时 `positionMerge` 必须与持仓方向一致（平多用 `long`，平空用 `short`），`close` 必须为 `true`，建议传入 `positionId`。

### 8.5.3 marginMethod 统一约束

- KTX 统一账户要求 **同一交易对所有仓位使用相同 marginMethod**
- 若现有 BTC 仓位为 `cross`，则 ETH 也只能用 `cross` 开仓
- 切换 marginMethod 需要先平掉所有仓位
- 错误码 `-12015`：`"It should be consistent with the existing marginMethod"`

### 8.5.4 minOrderSize 校验规则

- 产品信息中 `minOrderSize`（如 ETH=0.008）**仅对 mini 合约生效**
- **cross 模式下可下小于 minOrderSize 的量**（如 0.002 ETH 在 cross 下可成交）
- mini 合约约束：`mini=true` 时必须 `positionMerge=none && marginMethod=isolate && type=market`
- `_normalize_qty` 已放宽：qty < minOrderSize 时仅警告不拒绝，由交易所最终校验

### 8.5.5 保证金计算

```
名义价值 = qty × price
保证金   = 名义价值 / leverage
```

- 全仓(cross)：所有仓位共享保证金池
- 逐仓(isolate)：独立保证金，不与其他仓位互相补充
- 错误码 `-21301`：保证金不足（含所有仓位占用）

### 8.5.6 合约下单代码示例

```python
from app.services.live_trading.ktx import (
    KtxClient, POS_MERGE_LONG, POS_MERGE_SHORT,
    MARGIN_CROSS, CLOSE_OPEN, CLOSE_CLOSE,
)

swap = KtxClient(api_key="...", secret_key="...", market_type="swap")

# 开多 0.002 ETH，5x杠杆，全仓
swap.place_limit_order(
    symbol="ETH/USDT", side="buy", qty=0.002, price=2000.0,
    leverage=5, margin_method=MARGIN_CROSS,
    pos_side=POS_MERGE_LONG, close=CLOSE_OPEN,
)

# 开空 0.002 ETH，5x杠杆，全仓（多空双开）
swap.place_limit_order(
    symbol="ETH/USDT", side="sell", qty=0.002, price=2020.5,
    leverage=5, margin_method=MARGIN_CROSS,
    pos_side=POS_MERGE_SHORT, close=CLOSE_OPEN,
)

# 平多仓（需要先查询持仓获取 positionId）
positions = swap.get_positions(symbol="ETH_USDT_SWAP")
pos = [p for p in positions if p["side"] == "long"][0]
swap.place_limit_order(
    symbol="ETH/USDT", side="sell", qty=float(pos["quantity"]), price=2100.0,
    leverage=5, margin_method=MARGIN_CROSS,
    pos_side=POS_MERGE_LONG, close=CLOSE_CLOSE,
    position_id=str(pos["id"]),
)
```

---

## 9. 合约交易验证记录

已通过真实 API 完成以下合约交易操作验证：

| 功能 | 方法 | 验证结果 |
|------|------|----------|
| 合约行情查询 | `get_ticker()` | ✅ ETH/USDT 最新价 ~2010 USDT |
| 持仓查询 | `get_positions()` | ✅ 返回 side/quantity/entryPrice/marginMethod/leverage/id |
| 限价开空 | `place_limit_order(side="sell", pos_side="short")` | ✅ 0.008 ETH @ 2031.77, 5x杠杆 |
| 限价开多 | `place_limit_order(side="buy", pos_side="long")` | ✅ 0.002 ETH @ 2006.55, 5x杠杆 |
| 限价平多 | `place_limit_order(side="sell", pos_side="long", close=True)` | ✅ 0.004 ETH @ 2019.17 |
| 多空双开 | `place_limit_order(side="sell", pos_side="short", close=False)` | ✅ 0.0002 ETH @ 2020.5 |
| 合约撤单 | `cancel_order()` | ✅ state=0, status=cancelled |

---

## 10. 测试

### 9.1 真实API测试（ktx-case）

```bash
cd backend_api_python
# 运行全部现货测试
python3 -m pytest tests/ktx-case/ -v -s

# 单独运行
python3 -m pytest tests/ktx-case/test_spot_buy_limit.py -v -s    # 限价买入+撤单
python3 -m pytest tests/ktx-case/test_cancel_order.py -v -s       # 撤单
python3 -m pytest tests/ktx-case/test_spot_market_sell.py -v -s   # 市价卖出
python3 -m pytest tests/ktx-case/test_swap_limit_short.py -v -s  # 合约限价开空
python3 -m pytest tests/ktx-case/test_swap_limit_long.py -v -s   # 合约限价开多（逐仓→全仓回退）
python3 -m pytest tests/ktx-case/test_swap_close_long.py -v -s  # 合约查询持仓+限价平多
python3 -m pytest tests/ktx-case/test_swap_hedge_short.py -v -s # 合约多空双开（开空）
```

**注意：** `ktx-case` 目录下的测试会调用真实 KTX API，涉及真实交易操作。测试文件通过临时写入 stub 的方式绕过 `app.services.__init__.py` 的 pandas 依赖。

### 9.2 Mock 单元测试

```bash
cd backend_api_python
python3 tests/run_ktx_tests.py   # 43 个 mock 测试
```

---

## 11. Client 初始化

```python
from app.services.live_trading.ktx import KtxClient, POS_MERGE_LONG, MARGIN_CROSS, CLOSE_OPEN

# 现货
spot_client = KtxClient(
    api_key="your_api_key",
    secret_key="your_secret_key",
    market_type="spot",
)

# 合约
swap_client = KtxClient(
    api_key="your_api_key",
    secret_key="your_secret_key",
    market_type="swap",  # 默认值
)

# 合约下单示例：开多 0.002 ETH，5x杠杆，全仓
swap_client.place_limit_order(
    symbol="ETH/USDT",
    side="buy",
    qty=0.002,
    price=2000.0,
    leverage=5,
    margin_method=MARGIN_CROSS,
    pos_side=POS_MERGE_LONG,
    close=CLOSE_OPEN,
)
```

`market_type` 决定 `_ktx_market_param()` 的默认返回值：`spot` → `"spot"`，`swap` → `"lpc"`。