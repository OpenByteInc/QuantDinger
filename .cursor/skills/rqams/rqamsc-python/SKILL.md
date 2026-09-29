---
name: rqamsc-python
description: 使用 rqamsc Python SDK 查询 API、编写最小脚本、导入交易流水和结算流水、定位工作空间与初始化问题。当任务涉及 RQAMS 产品、产品组、头寸、交易流水、估值表或相关 Python 接口时使用。
---

# rqamsc-python

## 使用方法

### 文档缓存

查文档前，先在 skill 目录运行：

```powershell
python scripts/init_skill.py
```

`init_skill.py` 管理文档缓存，不负责执行业务登录。它会复用本地 `cache/`；只有核心文档或索引缺失、缓存超过 7 天，或显式传入 `--force-refresh` 时，才从线上 `document-index.txt` 定位并下载 `rqamsc` Markdown 源文档，然后刷新缓存并重建索引。

常用命令：
- 普通查文档：`python scripts/init_skill.py`
- 强制刷新文档：`python scripts/init_skill.py --force-refresh`
- 初始化后顺带展示环境摘要：`python scripts/init_skill.py --show-env`

### 运行时初始化

- 在执行任何需要 Python SDK 的业务脚本前，先确认客户机器上有可用 Python，并确认目标 Python 环境已安装 `rqamsc`。如果确认 `rqamsc` 未安装，先提示用户在目标 Python 环境安装 `rqamsc`，不要继续执行业务脚本。
- 不要假设客户机器一定有 `python` 命令；如果没有 Python，先提示用户安装或指定可用解释器。
- 如果设置了 `RQAMSC_PYTHON`，优先检查该解释器环境；否则检查当前默认 Python。
- 任何会真正执行 `rqamsc` 功能的脚本或代码，在进入业务逻辑前都必须先走固定运行时初始化入口，不要在各处重复手写 `rqamsc.init(...)`
- 固定运行时初始化入口为 `scripts/rqamsc_runtime.py` 中的 `initialize_rqamsc()`
- `scripts/inspect_env.py` 负责展示环境摘要，并复用上述固定初始化入口；它是检查脚本，不是让其他脚本复制粘贴初始化实现的模板

- Python 解释器优先级：
  - 如果设置了 `RQAMSC_PYTHON`，优先使用该解释器
  - 否则使用当前默认 `python`
- 运行时凭据优先使用共享 profile：
  - `rqamsc setup` 写入的 `rqams-cli` profile 是 CLI 和 Python SDK 的共同配置来源
  - Python 通过 `RQAMSC_PROFILE` 选择 profile
  - 如果未设置 `RQAMSC_PROFILE`，则使用 `rqams-cli` 配置文件里的当前 active profile 或顶层默认配置
  - 如果设置了 `RQAMS_CLI_CONFIG`，Python 读取同一个配置文件路径
- 环境变量只保留必要选择项：
  - `RQAMSC_PROFILE`
  - `RQAMS_CLI_CONFIG`
  - `RQAMSC_SSL_VERIFY`
  - `RQAMSC_PYTHON`
- 不要维护另一套账号密码环境变量；账号、密码、AMS 地址和 workspace 只从共享 profile 读取
- 如果 profile 无法提供用户名、密码或 AMS 地址，提示用户先创建或修复共享 profile：agent 在本地临时目录生成登录 payload 模板文件，例如 `D:\tmp\rqams_setup_<profile>.json`，用户在文件中填写密码后，由 agent 执行 `rqamsc setup --payload @<payload-file>`
- 如果设置了 `RQAMSC_SSL_VERIFY`，优先使用该值
- 如果未设置 `RQAMSC_SSL_VERIFY`：
  - profile 中的 AMS 地址以 `https://` 开头时，默认 `ssl_verify=True`
  - profile 中的 AMS 地址以 `http://` 开头时，默认 `ssl_verify=False`
- 如果 profile 中保存了 workspace，初始化后自动切换到该 workspace
- 初始化完成后，只简洁告知当前：
  - Python 环境
  - 配置来源和 profile
  - 登录账号
  - AMS 地址
  - 当前 workspace 名称
- 同时提示用户：如需切换账号或 workspace，优先切换 `RQAMSC_PROFILE`；如需切换解释器，可设置 `RQAMSC_PYTHON`
- 不在初始化阶段主动展开版本影响说明
- 业务功能文档不重复强调 workspace，默认以上述初始化结果为准
- 环境摘要默认每个会话只向用户展示一次；只有当前会话第一次进入 `rqamsc` 任务、Python 环境变化、AMS 地址变化、workspace 变化、初始化失败，或用户明确要求查看当前环境时，才重复展示。

### 版本处理

- 默认优先兼容本地已安装的 `rqamsc` 版本
- 当用户提出具体需求时，再按需检查该需求涉及的接口是否受版本变更影响
- 如果文档路径与本地版本存在差异，应优先提示用户当前按本地版本处理
- 只有在用户明确确认要升级后，才帮助用户升级 `rqamsc`
- 如需确认版本或接口可用性，直接检查目标 Python 环境中已安装的 `rqamsc` 包；如果设置了 `RQAMSC_PYTHON`，以该解释器环境为准。
- Python API 字段、示例模板和默认值按当前 Python `rqamsc` 版本独立维护，不要求与 `rqams-cli` 命令字段或 CLI 产品创建模板一致。跨路径比较时只比较业务意图和结果语义，不用字段名强行对齐。

### 查找文档

1. 优先在 `cache/api_index/api_index.md` 中查找具体接口
2. 如果接口索引不足以定位，再到 `cache/api_index/section_index.md` 中查找相关章节
3. 根据索引中的 `line_range` 到 `cache/api_docs/api-rqamsc.md` 读取对应段落
4. 只有索引无法定位时，才直接在 `cache/api_docs/api-rqamsc.md` 中搜索

索引文件顶部的 `Source` 行标明 `line_range` 对应的源文档文件。

以下源文档由 `scripts/init_skill.py` 按线上文档拆分生成到本地 `cache/`：
- `cache/api_docs/api-rqamsc.md`
- `cache/api_docs/changelogs.md`
- `cache/api_docs/manual-rqamsc.md`
- `cache/api_docs/tutorial-rqamsc.md`
- `cache/api_docs/rqamsc-faq.md`

当前索引主题包括：
- `api_index.md`
- `section_index.md`
- `changelog_index.md`

### 批量查询

如果 API 文档或函数签名显示接口支持批量输入，优先按批量接口查询。用户未指定批量大小时，默认每批 10 个标的；每批结果按原始输入顺序合并，并保留失败批次或失败标的的信息。批量失败且错误指向请求规模、字段格式或单个标的问题时，先缩小批量或逐项重试，再向用户报告不可恢复的失败项。

### 其他注意事项

- 优先使用公开 API 和 README 中已有工作流，不要猜测内部调用方式
- 涉及写入操作时，先确认目标产品和输入字段
- 涉及托管事件创建、更新、上传或删除时，先阅读上级 skill 的 `references/custodian_event_workflow.md`；申购、赎回事件写入前必须确认或补齐申赎开放日和 4 位申赎单位净值
- 示例代码默认是接口模板，除非明确标注，否则不视为已完成端到端验证
- 常见经验性问题参考：
  - `references/pitfalls.md`
