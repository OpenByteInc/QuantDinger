---
name: rqams
description: 处理 RQAMS 数据查询和操作时使用。标准产品、workspace、交易流水、估值表、持仓报表、模拟交易、对账、报表和分析任务优先路由到 rqams-cli；需要 Python SDK 脚本、多 API 组合处理、本地 Python 环境诊断或 CLI 未覆盖接口时路由到 rqamsc-python。
---

# RQAMS

此 skill 是 RQAMS 的统一入口，负责在两个执行路径之间选择：

- `rqams-cli/`：使用本地 `rqamsc` CLI 处理标准 RQAMS 操作。
- `rqamsc-python/`：使用 Python SDK 编写脚本、处理复杂工作流、诊断环境，或调用 CLI 尚未覆盖的接口。

处理具体 RQAMS 任务前，先按 `references/ams_valuation_flow.md` 理解用户需求在 AMS 估值主链路中的位置：对象层、输入层、计算层或结果层。确定业务位置后，再路由到 CLI 或 Python 路径。

涉及托管事件的创建、更新、上传、删除、排查，或用户提到申购、赎回、分红、费用、现金/应收应付调整时，必须先阅读 `references/custodian_event_workflow.md`。该文档说明托管事件写入前的日期、金额、申赎开放日和申赎单位净值确认规则。

涉及对账、reconciliation、估值表差异、持仓差异、净值差异、现金/应收应付差异或需要判断是否使用 `auto` 对账时，必须先阅读 `references/reconciliation_workflow.md`。该文档是工具无关的对账处理准则，适用于 `rqams-cli` 和 `rqamsc-python`。

## 首次初始化

当用户首次安装 `rqams` skill 时，先按以下顺序完成初始化，再处理具体业务任务：

1. 安装本地工具：

```powershell
npm install -g @ricequant2026/rqams-cli --include=optional
python -m pip install rqamsc
```

如果用户指定了目标 Python 解释器，使用该解释器执行 `-m pip install rqamsc`；如果设置了 `RQAMSC_PYTHON`，后续 Python 路径也以该解释器为准。安装后必须验证：

```powershell
rqamsc --version
python -c "import rqamsc; print(rqamsc.__file__)"
```

2. 初始化 skill 文档缓存。分别进入两个子 skill 目录运行初始化脚本，不要假设 `cache/` 已经存在：

```powershell
cd rqams-cli
python scripts/init_skill.py
cd ..\rqamsc-python
python scripts/init_skill.py --show-env
cd ..
```

若需要强制刷新文档缓存，使用对应子目录下的 `python scripts/init_skill.py --force-refresh`。

3. 配置共享 profile。CLI 和 Python SDK 统一使用 `profile` 管理登录态和 workspace。Agent 负责执行配置，不要让用户自己输入命令。默认不要在聊天中索取密码；agent 先确认非敏感字段，包括 profile 名称、AMS 服务地址、用户名，以及可选的 workspace 名称或 ID，然后在本地临时目录生成登录 payload 模板文件，让用户直接在该文件中补全或修正敏感字段。

```powershell
rqamsc setup --payload @D:\tmp\rqams_setup_<profile>.json
```

登录 payload 模板文件的推荐位置是 `D:\tmp\rqams_setup_<profile>.json`；如果 `D:\tmp` 不可用，则使用系统临时目录下的同名文件。Agent 创建模板后必须告知用户完整路径，并说明只需要填写或确认以下 JSON 字段：

```json
{
  "profile": "profile-name",
  "base_url": "https://...",
  "username": "account",
  "password": "fill-password-here",
  "workspace_name_or_id": "optional-workspace"
}
```

用户确认文件已填写后，agent 执行 `rqamsc setup --payload @<payload-file>`。`rqamsc setup` 会把该 profile 的登录态保存到本地 CLI 配置中，便于后续 CLI 命令和 Python SDK 复用。后续 CLI 命令在 payload 顶层传同一个 `profile`；Python SDK 路径通过 `RQAMSC_PROFILE` 选择同一个 profile。Agent 处理凭据时必须避免在回复、仓库文件、skill 文件或可长期保留的日志中回显真实账号、密码或 session；临时 payload 文件只用于本次配置，配置完成后删除。若用户未提供 workspace，先完成登录，再用只读命令查询 workspace 列表并让用户选择，随后由 agent 执行 workspace 配置。

4. 初始化完成后，用只读命令确认状态：

```powershell
rqamsc get current-workspace --payload '{"profile":"..."}'
rqamsc schema list
```

如果后续任务走 Python SDK 路径，按 `rqamsc-python/SKILL.md` 的运行时初始化规则设置 `RQAMSC_PROFILE`，让 Python 使用同一个 profile。不要维护另一套账号密码环境变量；账号、密码、AMS 地址和 workspace 只来自共享 profile。

## 路由规则

当用户要查询、创建、更新、删除、上传、下载、重算或汇总标准 RQAMS 资源时，优先使用 `rqams-cli`。标准资源包括 workspace、产品、产品组、交易流水、估值表、持仓报表、托管事件、份额事件、自定义对象、模拟交易、对账、分析和报表。

当用户明确要求 Python 代码、需要可复用脚本或 notebook 式流程、需要组合多个 API 做自定义处理、需要检查本地 Python `rqamsc` 环境，或 CLI schema 未覆盖目标 API 时，使用 `rqamsc-python`。

如果两条路径都能完成任务，一次性操作使用 CLI，复杂数据处理使用 Python。

## 批量查询策略

当目标接口支持批量查询多个产品、产品组、日期、ID、名称或其他资源标识时，优先使用批量参数，而不是逐个资源串行查询。若用户没有指定批量大小，默认每批 10 个标的；每批完成后汇总结果，再继续下一批。只有接口文档、运行时 schema、服务端报错或用户明确要求显示单次请求数量限制时，才调整批量大小。

分批查询时要保留输入顺序和失败项信息：成功结果按原始输入标识合并；单批失败时先记录该批输入、错误码和错误消息，再根据错误类型判断是否缩小批量或改为逐项重试。不要在顶层维护具体接口的批量字段名，字段名仍以 `rqams-cli` 运行时 schema、CLI 缓存文档或 `rqamsc-python` API 文档为准。

## 版本与字段来源

顶层 `rqams` 只维护工具选择、业务链路判断和通用安全准则，不维护具体命令字段、API 字段或产品创建模板字段。

`rqams-cli` 和 `rqamsc-python` 可能连接不同版本的工具、SDK 或后端服务，字段和默认模板允许各自演进。选择执行路径后，以该路径自己的运行时 schema、缓存文档和版本检查结果为准；不要为了保持 CLI 与 Python 一致而补齐或改写字段。

通用的结果查询决策标准可以复用，例如先判断用户要快照、时间序列、横截面汇总、明细列表还是分析结果；但具体可用字段、默认值、批量能力和返回结构必须回到对应子路径确认。

## CLI 路径

使用 CLI 路径前，先阅读 `rqams-cli/SKILL.md`。

首次安装或重装 skill 后，不要假设 `rqams-cli/cache/` 已存在；按子 skill 要求先运行 `python scripts/init_skill.py` 生成文档索引。如果本机未安装 `rqamsc`，按子 skill 的 bootstrap 规则先安装 CLI 并验证 `rqamsc --version`。首次配置 RQAMS 登录态时，由 agent 创建本地登录 payload 模板文件并告知用户路径；用户在文件里填写密码后，agent 执行 `rqamsc setup`。不要要求用户手动输入命令或手动设置一组环境变量。后续 CLI 业务 payload 顶层应携带同一个 `profile`。

以运行时 schema 作为命令契约：

```powershell
rqamsc schema list
rqamsc schema get --payload '{"command":"get product-list"}'
```

再按统一格式调用：

```powershell
rqamsc <verb> <resource> --payload <json|@file|->
```

## Python 路径

使用 Python 路径前，先阅读 `rqamsc-python/SKILL.md`。

按子 skill 说明使用 `rqamsc-python/scripts/` 中的脚本初始化或检查 Python 环境。

## 安全要求

对于创建、更新、删除、上传、重算等写操作，除非用户已经给出精确指令，否则执行前必须明确目标资源和关键 payload 字段。

对账写操作必须遵守 `references/reconciliation_workflow.md`：先只读诊断并向用户报告差异、拟写入/删除内容、影响范围和预期效果，用户确认后再执行。

托管事件写操作必须遵守 `references/custodian_event_workflow.md`：尤其是申购、赎回事件，不要凭常识推断上一交易日；写入前必须确认或补齐申赎开放日和 4 位申赎单位净值。

不要在顶层文件重复维护命令字段或 API 对象 schema。CLI 运行时 schema、`rqams-cli/scripts/init_skill.py` 生成的 CLI 文档缓存、Python 子 skill 初始化脚本生成的索引和通用 reference 才是事实来源。
