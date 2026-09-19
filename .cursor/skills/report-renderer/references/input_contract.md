# report-renderer 输入契约

`report-renderer/scripts/render_report.py` 接收一个 Markdown 文件并输出单文件 HTML。

## 1. 输入文件

推荐输入是由其他 research skill 生成的 Markdown 报告，例如：

- `morning-note`
- `thesis-tracker`
- `idea-generation`
- `earnings-preview`
- `sector-overview`

## 2. 支持的 Markdown 子集

渲染器内置解析器，当前保证覆盖：

- ATX 标题：`#` / `##` / `###` / `####`
- 无序列表：`- item` / `* item`
- 有序列表：`1. item`
- Markdown 表格
- 普通段落
- 分隔线：`---`
- 块引用：`> quote`
- 代码块：````` ```lang ... ``` `````
- 行内粗体：`**text**`
- 行内斜体：`*text*`
- 行内代码：`` `code` ``
- 表格单元格中的原生 `<br>`

## 3. 元数据抽取

渲染器会尝试从 Markdown 前几行提取：

- 主标题
- 报告日期
- 行业 / 公司 / 覆盖范围等摘要字段

若未识别到元数据：

- 标题退回到文件名
- 报告日期显示为 `未提供`

## 4. 输出约定

- 默认输出路径为输入文件同名 `.html`
- 输出是完整 HTML5 文档
- CSS 由 `assets/report.css` 内联注入，不依赖外部样式资源

## 5. 非目标能力

当前不保证完整支持：

- 嵌套列表
- 复杂表格合并单元格
- 任意 HTML 嵌入
- 脚注、任务列表、LaTeX
