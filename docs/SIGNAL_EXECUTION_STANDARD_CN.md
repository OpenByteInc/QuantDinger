# QuantDinger 信号与执行标准（SSOT）

**版本**：1.0  
**状态**：现行  
**适用范围**：所有基于 `IndicatorStrategy`（指标 Python + 保存为策略）的回测与实盘  
**关联实现**：`BacktestService`、`TradingExecutor`、`validate_code_safety` / `verifyCode`  
**开发指南**：[STRATEGY_DEV_GUIDE_CN.md](./STRATEGY_DEV_GUIDE_CN.md)（教程与示例）

---

## 1. 目的

平台会同时运行**多种不同逻辑**的策略。若没有统一标准，会出现：

- 同一套 `buy`/`sell` 在不同策略里含义不同；
- 回测按「收盘 + 下一根开盘」成交，实盘按「未收盘 K + 立即平仓」执行；
- 指标内止盈止损与 `# @strategy trailingEnabled` 叠加，导致重复平仓与拒单。

本标准定义：**策略作者写什么、引擎如何解释、回测与实盘必须如何对齐**。  
所有新策略 **SHOULD** 遵循；存量策略 **SHOULD** 按第 9 节迁移。

---

## 2. 术语

| 术语 | 含义 |
|------|------|
| **信号 K 线** | 产生布尔信号的那根 K 线（时间戳 = 该 bar 收盘时刻） |
| **成交 K 线** | 订单实际执行所锚定的 K 线（默认 = 信号 K 的下一根） |
| **边缘触发** | 仅在与上一根 K 相比由 false→true 时记为信号 |
| **重绘** | 未收盘 K 上条件随行情变化而反复成立/消失 |
| **退出负责人** | 唯一主导平仓逻辑的层：指标信号 **或** 引擎风控，不可并列窄规则 |

---

## 3. 策略形态选型（必须先选）

| 形态 | 适用 | 不适用 |
|------|------|--------|
| **A. 简单两路** `buy` / `sell` | 金叉/死叉、单方向或对称反转；无独立「仅平仓」语义 | 多空分离 tp/sl、状态机、同 bar 先平后开需写清 |
| **B. 专业四路** `open_long` / `close_long` / `open_short` / `close_short` | 状态机、触及止盈止损、多空分离、与回测队列一一对应 | 极简趋势（可用 A，但 B 更利于长期维护） |
| **C. ScriptStrategy** `on_bar` + `ctx` | 强依赖持仓、分批、冷却、bot 网格 | 纯历史序列可表达的指标信号 |

**平台推荐默认值**：新上架、多空或带指标内退出的策略 → **形态 B（四路）**。

---

## 4. 信号输出标准（IndicatorStrategy）

### 4.1 通用 MUST

1. **MUST** `df = df.copy()` 作为首行可变操作。  
2. **MUST** 提供 `output` 字典（含 `name`、`plots`；`signals` 可选用于图表标记）。  
3. **MUST** 执行列与 `len(df)` 一致、类型为 `bool`（`fillna(False).astype(bool)`）。  
4. **MUST** 对执行列做**边缘触发**（见 4.3），除非策略说明中明确声明「连续持仓信号」并经产品审核。  
5. **MUST NOT** 使用 `shift(-1)` 及任何未来数据引用。  
6. **MUST NOT** 在单脚本内混用「形态 A 执行列」与「形态 B 执行列」作为成交依据（可同时保留 `buy`/`sell` 全 false 仅作兼容）。

### 4.2 形态 A：两路 `buy` / `sell`

| `tradeDirection` | `buy=True` 含义 | `sell=True` 含义 |
|------------------|-----------------|------------------|
| `long` | 开多 | 平多 |
| `short` | 平空 | 开空 |
| `both` | 开多；若持空则**先平空再开多** | 开空；若持多则**先平多再开空** |

**MUST NOT** 在 `both` 下把 `buy` 理解为「仅平空」或「仅平多」。  
若需**只平仓不反手**，**MUST** 使用形态 B 的 `close_*`。

### 4.3 形态 B：四路（推荐标准）

| 列 | 含义 |
|----|------|
| `open_long` | 开多（或加多，若引擎配置允许） |
| `close_long` | 平多（全部或按 reduce 配置） |
| `open_short` | 开空 |
| `close_short` | 平空 |

**MUST** 满足：

- 四列均存在且为 bool。  
- **SHOULD** 同 bar 优先级：`close_*` 先于 `open_*`（脚本内互斥或交给引擎「先平后开」）。  
- **SHOULD NOT** 同 bar 同时 `open_long` 与 `open_short` 为 true（反手请用 `close_*` + 对侧 `open_*`，或分两根 K）。  
- 使用四路时，引擎按**显式信号**处理（非 buy/sell 的 both 隐式映射）。

边缘触发模板：

```python
def edge(s: pd.Series) -> pd.Series:
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1).fillna(False)

df['open_long']   = edge(raw_open_long)
df['close_long']  = edge(raw_close_long)
df['open_short']  = edge(raw_open_short)
df['close_short'] = edge(raw_close_short)
```

`output['signals']` 仅用于展示；**成交只认上述执行列**。

### 4.4 `tradeDirection` 与四路的关系

| `tradeDirection` | 行为 |
|----------------|------|
| `long` | 忽略 `open_short` / `close_short`（或校验警告） |
| `short` | 忽略 `open_long` / `close_long` |
| `both` | 四路均有效；**不**再对 `buy`/`sell` 做反手映射 |

---

## 5. 退出与风控标准（解决重复平仓）

每个策略 **MUST** 在说明或注释中声明「退出负责人」之一：

| 模式 | 脚本 | `# @strategy` | 说明 |
|------|------|---------------|------|
| **指标退出** `exit_owner: indicator` | 输出 `close_*`，或两路中明确等价的退出语义 | `trailingEnabled false`；不要依赖 `stopLossPct` / `takeProfitPct` / trailing | 触及型、中轨/轨道 tp/sl；当前实现会关闭服务端价格退出 |
| **引擎退出** `exit_owner: engine` | 只输出入场，或只输出趋势反转用的结构性 `close_*` | `trailingEnabled` / `stopLossPct` / `takeProfitPct` 按需 | 固定止损止盈、追踪止损、简单趋势 |

**MUST NOT**：指标内窄 tp/sl **且** `trailingEnabled true` 窄移动止损（易造成 `server_trailing_stop` 后再发 `close_*` → 数量为 0）。

启用 trailing 时：**SHOULD** 关闭指标内同方向的 tp/sl 布尔列，或关闭 trailing。

当前后端只支持 `exit_owner: indicator` 与 `exit_owner: engine`。不要生成或上架 `exit_owner: layered`；若确实需要“指标退出 + 引擎兜底”的混合方案，必须先产品评审并明确改造执行器语义。

---

## 6. 执行契约（回测与实盘对齐）

以下配置在「保存后的策略」`trading_config` 中生效；**回测与实盘 MUST 使用同一套规范化结果**。

### 6.1 信号时刻（抗重绘）

| 项 | 标准值 | 说明 |
|----|--------|------|
| `signal_mode` | **`confirmed`** | 入场信号仅来自**上一根已收盘** K |
| `exit_signal_mode` | **`confirmed`** | 出场信号同上；与回测一致 |
| 形成中 K | **不参与下单** | 实盘可刷新最后一根 K 做展示，但不应作为默认成交依据 |

**MAY** 在产品层提供 `aggressive` 供研究；上线组合策略 **SHOULD NOT** 默认 aggressive。

### 6.2 成交时刻

| 项 | 标准值 |
|----|--------|
| 回测 `signalTiming` / 策略快照 | **`next_bar_open`**（默认） |
| 语义 | 信号 K 收盘确认 → **下一根 K 开盘价**（±滑点）成交 |

**SHOULD NOT** 在未改回测配置的情况下，实盘使用 `exit_trigger_mode=immediate` 做指标策略。

### 6.3 同 bar 多信号

| 规则 | 说明 |
|------|------|
| 优先级 | `close_*` > `reduce_*` > `open_*` > `add_*` |
| 反手 | **方案 R1（推荐）**：同 bar 只平不開，**下一根**再 `open_*`；**方案 R2**：同 bar 先平后开（与历史 both 回测一致，需策略与回测均启用） |
| 每 tick | 指标模式默认每 tick 最多执行 **1** 个外部信号；反手若用 R2，引擎可在单次 `open_*` 内先平对侧 |

策略说明 **MUST** 注明采用 R1 或 R2。

### 6.4 平仓数量

| 规则 | 说明 |
|------|------|
| 数量来源 | 平仓 **MUST** 以本地持仓与交易所持仓较大者为准（sync + resolve） |
| 数量为 0 | **MUST** 再 sync 一次后重试；仍为空则拒单并记日志，**MUST NOT** 静默跳过 |

---

## 7. 配置标准（`# @strategy` / `# @param`）

### 7.1 `# @strategy`（SHOULD 显式声明）

| Key | 类型 | 说明 |
|-----|------|------|
| `tradeDirection` | `long` \| `short` \| `both` | 与执行列一致 |
| `entryPct` | 0.01–1.0 | 开仓资金占比（`1` = 100%） |
| `stopLossPct` / `takeProfitPct` | 0–1 | **标的涨跌幅**阈值；亚 1% 写 `0.001` 等小数 |
| `trailingEnabled` | bool | 见第 5 节 |
| `trailingStopPct` / `trailingActivationPct` | 0–1 | 追踪回撤/激活阈值（同为价格涨跌幅，**不除杠杆**） |

**MUST NOT** 在 `@strategy` 中写 `leverage`（由产品 UI / 策略配置）。

### 7.2 推荐策略头注释块（复制模板）

```python
# --- QuantDinger execution contract (v1) ---
# signal_form: four_way          # two_way | four_way
# exit_owner: indicator          # indicator | engine
# flip_mode: R1                  # R1=close bar then open next bar | R2=same bar flip
# tradeDirection: both
# @strategy entryPct 1
# @strategy trailingEnabled false
```

---

## 8. 校验与发布清单

### 8.1 保存 / `verifyCode` 时（引擎 SHOULD）

- [ ] 执行列存在且长度正确  
- [ ] 禁止不安全代码（见 `safe_exec`）  
- [ ] 警告：`trailingEnabled` + 高比例 `close_*`  
- [ ] 警告：同 bar `open_long` & `open_short`  
- [ ] 警告：连续多根同向 `open_*` 无 edge（可选启发式）

### 8.2 上线前人工清单

- [ ] 已选形态 A/B/C 并写在策略说明  
- [ ] 已声明退出负责人  
- [ ] `signal_mode` / `exit_signal_mode` = `confirmed`  
- [ ] 回测成交时间为 next bar open，与预期一致  
- [ ] 对比最近 20 笔：回测信号时间 vs 实盘日志时间偏差可解释  
- [ ] 小仓位、单标的试跑 24h，无大量 `invalid amount (0.0)`

---

## 9. 存量策略迁移

| 优先级 | 特征 | 动作 |
|--------|------|------|
| P0 | `both` + `buy` 含 tp/sl + `trailingEnabled` | 关 trailing 或改四路 `close_*` |
| P1 | 触及型逻辑 + 实盘差异大 | 改 `confirmed` + 四路 + edge |
| P2 | 纯金叉死叉两路 | 可保留形态 A，补全契约注释 |
| P3 | 已是四路 | 补 edge、退出负责人、配置对齐 |

**MAY** 在策略市场标注「契约 v1 已认证」徽章，便于用户筛选。

---

## 10. 策略分级（运营可选）

| 等级 | 要求 |
|------|------|
| **L1 基础** | 通过 `verifyCode` + 沙箱 |
| **L2 对齐** | 四路或声明两路 + `confirmed` + 退出负责人明确 |
| **L3 生产** | L2 + 回测/实盘时间偏差评审 + 7 日模拟盘无异常拒单 |

---

## 11. 与实现的对照（便于研发）

| 标准条 | 代码锚点 |
|--------|----------|
| 形态 B 四路 | `TradingExecutor._execute_indicator_with_prices`；`BacktestService` norm_signals |
| both 两路映射 | 仅当无四路且 `buy`/`sell` 时；`_indicator_both_mode` |
| confirmed | `signal_mode` / `exit_signal_mode` 检查集 |
| 平仓重试 | `PendingOrderWorker` + `resolve_reduce_only_quantity` |
| 静态安全 | `app/utils/safe_exec.py` |

标准变更时 **MUST** 同步更新本文件版本号与 CHANGELOG。

---

## 12. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0 | 2026-05 | 首版：四路推荐、执行契约、退出负责人、迁移与分级 |
