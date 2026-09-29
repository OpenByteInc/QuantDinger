---
name: rqams-cli
description: 使用本地 rqamsc CLI 查询和操作 RQAMS 时使用，覆盖产品、workspace、交易流水、估值表、持仓报表、模拟交易、对账、报表和分析数据等标准任务。
---

# RQAMS CLI

当用户请求可由本地 `rqamsc` 命令完成的 RQAMS 数据查询或操作时，使用此 skill。

## 使用方法

### 文档缓存

查文档前，先在 skill 目录运行：

```powershell
python scripts/init_skill.py
```

安装 skill 时不会自动生成 `cache/` 下的文档和索引。每次首次使用本 skill 查询 CLI 文档前，必须先运行上述普通初始化命令；如果该命令首次耗时较长，通常是在同步远端文档，不代表 RQAMS 业务接口慢。

`init_skill.py` 管理 CLI 文档缓存。它会复用本地 `cache/`；只有文档或命令索引缺失、缓存超过 7 天，或显式传入 `--force-refresh` 时，才从 GitHub 同步 CLI 文档到本地 `cache/` 并生成命令索引。

常用命令：
- 普通查文档：`python scripts/init_skill.py`
- 强制刷新文档：`python scripts/init_skill.py --force-refresh`

### 前置检查

先确认 CLI 可用：

```shell
rqamsc --version
```

如果本机没有 `rqamsc`，先引导或执行 CLI 安装，再继续后续步骤。默认 bootstrap 命令：

```powershell
npm install -g @ricequant2026/rqams-cli --include=optional
```

安装完成后必须重新运行 `rqamsc --version` 验证。若安装失败或提示平台包缺失，再查 `README.md` 或缓存文档中的安装排错；不要把更多安装分支复制到 skill 中。需要联网或全局写入时，按当前工具权限机制请求用户确认。

首次配置登录态和 workspace 时，使用 profile 作为统一配置句柄。Agent 负责配置，不要让用户自己输入命令，也不要默认在聊天中索取密码。Agent 先确认 profile、AMS 服务地址、用户名和 workspace 等非敏感信息，再在本地临时目录生成登录 payload 模板文件，让用户直接在文件中填写密码。

```shell
rqamsc setup --payload @D:\tmp\rqams_setup_<profile>.json
```

登录 payload 模板文件推荐放在 `D:\tmp\rqams_setup_<profile>.json`；如果 `D:\tmp` 不可用，则使用系统临时目录下的同名文件。Agent 创建模板后必须告知用户完整路径，并说明需要填写或确认的 JSON 字段。payload 必须包含 `profile`，例如：

```json
{"profile":"acct-a","base_url":"https://...","username":"account","password":"fill-password-here","workspace_name_or_id":"optional-workspace"}
```

用户确认文件已填写后，agent 执行 `rqamsc setup --payload @<payload-file>`。`setup` 会把该 profile 的登录态和 workspace 保存到本地配置。后续 CLI 业务命令在 payload 顶层传同一个 `profile`；Python SDK 通过 `RQAMSC_PROFILE` 复用同一个 profile。Agent 不要把真实账号、密码或 session 写入回复、仓库文件、skill 文件或长期日志；临时 payload 文件只用于配置，配置完成后删除。

### 事实来源

不要在 skill 中重复维护完整命令字段、安装命令或业务 payload 结构。事实来源按用途使用：

1. CLI 运行时 schema：当前二进制的机器可执行契约，用来确认目标命令是否存在、payload 字段形状、输出格式能力和兼容性。
2. 本地生成的缓存文档：业务语义、人类可读说明、调用协议、文件路径字段、示例和排错建议，位于 `cache/docs/`。
3. GitHub 文档：缓存刷新来源，地址为 `https://github.com/ricequant/rqams-cli/tree/master/docs`；日常查阅优先使用本地缓存。

运行时 schema 示例：

```powershell
rqamsc schema list
rqamsc schema get --payload '{"command":"get product-list"}'
```

执行任何非 `schema` / `--version` 的业务命令前，必须先查本地缓存文档确认命令命名、请求 payload、文件路径字段、输出 envelope/NDJSON 和业务数据格式；不要只根据 schema 字段名猜测接口语义：

- `cache/docs/rqams_cli_manual.md`
- `cache/docs/commands/*.md`
- `cache/doc_index/command_index.md`

其中 `cache/docs/rqams_cli_manual.md` 是总入口，解释统一调用协议、命令发现、认证与 workspace、文件上传/下载和 Agent 使用建议；`cache/docs/commands/*.md` 按业务域说明具体命令、字段和示例；`cache/doc_index/command_index.md` 用于快速定位命令所在文档和行号。若文档与运行时 schema 不一致，执行层面优先按运行时 schema，业务解释和示例仍参考缓存文档，并在回复中说明差异。

产品创建默认模板如果由 CLI 提供，模板字段和值只在 CLI 实现和 CLI 产品文档中维护。不要在 skill 中复写模板字段表，也不要要求它与 `rqamsc-python` 的 API 字段或模板保持一致；不同版本字段不一致时，以当前 CLI 运行时 schema 和 CLI 文档为准。

处理托管事件创建、更新、上传或删除前，先阅读上级 skill 的 `references/custodian_event_workflow.md`。申购、赎回事件写入前必须确认或补齐申赎开放日和 4 位申赎单位净值；具体 CLI 字段仍以运行时 schema 和缓存文档为准。

### 命令调用

统一命令格式：

```text
rqamsc <verb> <resource> --payload <json|@file|->
```

优先使用 JSON payload：

```shell
rqamsc <verb> <resource> --payload '{"field":"value"}'
```

Windows `cmd.exe` 不适合直接写复杂 inline JSON；优先改用 PowerShell，或把 payload 写入文件后使用 `--payload @payload.json`。

payload 较大时，使用文件或 stdin：

```powershell
rqamsc <verb> <resource> --payload @payload.json
Get-Content -Raw payload.json | rqamsc <verb> <resource> --payload -
```

默认输出是 JSON envelope。只有 `schema get` 显示命令支持 NDJSON 时，才使用 `"format":"ndjson"`。

### 批量查询

如果运行时 schema 或缓存文档显示命令支持批量字段，优先使用批量查询。用户未指定批量大小时，默认每批 10 个标的，并在 payload 顶层持续携带同一个 `profile`。每批结果按原始输入顺序合并；单批失败时记录该批输入和 JSON envelope 中的 `error.code` / `error.message`，必要时缩小批量或逐项重试。

### 安全要求

对于创建、更新、删除、上传、重算等写操作，除非用户已给出精确指令，否则执行命令前必须明确目标资源和关键 payload 字段。

托管事件写操作还必须遵守上级 `references/custodian_event_workflow.md`。不要只根据用户提供的日期和金额直接写入申购、赎回事件；先查询前序净值、说明补填口径并取得确认。

命令失败时，读取 JSON envelope 中的 `error.code` 和 `error.message`，总结可执行的问题和下一步，不要只贴原始日志。
