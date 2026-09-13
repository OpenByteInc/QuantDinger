---
name: rq-report-renderer
description: |
  将 Markdown 研究报告渲染为专业 HTML 文档。输入是其他 skill 已生成的 Markdown 报告，输出是可浏览、可打印的单文件 HTML。

  务必使用此技能当用户：
  - 明确要求把 Markdown 报告转换成 HTML
  - 想生成网页版研报或打印版页面
  - 已经有 `.md` 报告，只差最后的渲染步骤

  不适用场景：
  - 还没有 Markdown 报告
  - 需要 PDF / Word 导出
compatibility: "Requires python3. Does not rely on repo-level utils or external Markdown packages."
---

# RQ 股票研究 - HTML 报告渲染器

## 核心原则

- 输入必须是显式的 Markdown 文件路径，不能依赖固定目录
- skill 必须自包含，渲染逻辑、样式和输入契约都放在 `report-renderer/` 内
- 不依赖仓库级 `utils`
- 不要求外部 Markdown 库；脚本内置最小可用解析器，覆盖当前各 skill 的真实输出结构
- 输出必须保留标题、章节、表格、列表、代码样式与数据来源标注
- 若本机支持打开浏览器，应作为可选行为，而不是强制副作用

## 目标产出

- 单文件 HTML 报告
- 默认与输入 Markdown 同目录同名输出
- 适合桌面阅读与打印

## 目录结构

```text
report-renderer/
├── SKILL.md
├── scripts/
│   └── render_report.py
├── assets/
│   └── report.css
└── references/
    └── input_contract.md
```

## 输入契约

- 主输入：一个 Markdown 文件
- 可选参数：
  - 输出 HTML 路径
  - 覆盖标题
  - `--open`：渲染后尝试在默认浏览器打开

完整输入格式与支持的 Markdown 子集见 [references/input_contract.md](references/input_contract.md)。

## 工作流

### 步骤 1：准备输入文件

确认已有 Markdown 报告，例如：

```bash
INPUT_MD="skill-test-results/sector-overview/baijiu/output/sector_overview_baijiu_2026-04-03.md"
OUTPUT_HTML="${INPUT_MD%.md}.html"
```

### 步骤 2：执行渲染

主入口：

```bash
python3 report-renderer/scripts/render_report.py "$INPUT_MD" "$OUTPUT_HTML"
```

若希望渲染后尝试打开浏览器：

```bash
python3 report-renderer/scripts/render_report.py "$INPUT_MD" "$OUTPUT_HTML" --open
```

### 步骤 3：验证输出

- HTML 文件存在
- HTML 中包含 `<!DOCTYPE html>`
- 主标题、主要章节和表格都已渲染
- 不应残留原始 Markdown 表格分隔行（如 `| --- | --- |`）
- `数据来源：RQData，置信度5` 等来源标注仍保留

## 参数说明

- 第一个位置参数：输入 Markdown 文件
- 第二个位置参数：输出 HTML 文件，可省略
- `--title`：覆盖封面标题
- `--open`：渲染完成后尝试打开浏览器

## 报告质量要求

- 输出必须是完整 HTML，而不是片段
- 章节层级必须可导航，至少生成二级目录
- 表格必须渲染成 `<table>`
- 列表必须渲染成 `<ul>` / `<ol>`
- 粗体、斜体、行内代码与 `<br>` 必须保留
- 样式必须独立内联或由 skill 自带资源注入，不能依赖外部 CDN

## 常见错误

- 把完整渲染脚本直接塞回 `SKILL.md`，没有落地成可执行文件
- 只渲染段落，不支持 Markdown 表格
- 把列表项直接变成裸 `<li>`，没有外层 `<ul>` / `<ol>`
- 强依赖外部 Markdown 库，导致在最小环境里不可执行
- 依赖 repo 里的 `utils/html_renderer.py`
