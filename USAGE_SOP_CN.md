# PDF2JC 中文使用 SOP

本 SOP 说明如何从一篇生物医学论文的 PDF 和手动保存的完整 Figure 图片，生成一份可编辑的 journal club PowerPoint 草稿。

## 最终 PPT 在哪里？

完成全部步骤后，最终可编辑 PowerPoint 保存于：

```text
output/jc_draft.pptx
```

这个文件中的标题、正文、图注、panel 标签均为可编辑文本；每个 panel 图片也是独立对象，可以在 Microsoft PowerPoint 中单独移动、裁剪或替换。

> 注意：第一步的 `pdf2jc run` 也会生成一个同名 `output/jc_draft.pptx`，但那是旧的基线/mock 草稿。只有完成第 6 步 Presentation Builder 后写入的同名文件，才是由 Citation Mapping、Evidence Units 和 Slide Objects 驱动的最终版本。

## 一、开始前准备

需要：

- Python 3.10 或更高版本。
- 一个命令行工具：macOS 使用 Terminal，Windows 使用 PowerShell。
- 要处理的论文 PDF。
- 从论文中手动保存的每一张完整 Figure 图片。

最终 PowerPoint 渲染目前还需要 Node.js 和 presentation artifact runtime。若第 6 步提示找不到 renderer 或 Node.js，请先完成第 1 到第 5 步的全部 QC 文件检查；这说明公开版的 PPT 渲染环境尚未在你的电脑上配置完整。

## 二、下载和安装

打开 Terminal，逐行运行：

```bash
git clone https://github.com/casperzhao23-cpu/pdf2jc.git
cd pdf2jc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

以后每次重新打开 Terminal 时，先回到项目文件夹并启用虚拟环境：

```bash
cd pdf2jc
source .venv/bin/activate
```

安装完成后，可用以下命令确认工具可用：

```bash
pdf2jc --help
```

## 三、准备输入文件

在项目最外层创建以下结构：

```text
input/
├── paper.pdf
├── expected_panels.json          # 可选
└── figs/
    ├── fig1.png
    ├── fig2.png
    ├── fig3.jpg
    └── fig4.tif
```

创建文件夹：

```bash
mkdir -p input/figs
```

### 1. 论文 PDF

把要分析的论文放到：

```text
input/paper.pdf
```

### 2. 完整 Figure 图片

请手动从论文中保存每一张**完整 Figure**，放到：

```text
input/figs/
```

支持：`.png`、`.jpg`、`.jpeg`、`.tif`、`.tiff`。

文件名决定 Figure ID：

| 文件名 | 系统识别为 |
| --- | --- |
| `fig1.png` | `Fig1` |
| `figure2.jpg` | `Fig2` |
| `Figure_3.tif` | `Fig3` |

不要上传单独 panel。`fig1.png` 应该是包含 A、B、C 等所有 panel 的完整 Figure 1。

### 3. 可选：预期 panel 数量

可建立文件 `input/expected_panels.json`：

```json
{
  "Fig1": 7,
  "Fig2": 9,
  "Fig3": 6
}
```

它不会改变图像，只用于检查系统检测出的 panel 数量是否符合你的预期。数量不一致时，系统会标记 `needs_manual_review`。

## 四、运行 PDF 提取和 Panel Detection

运行：

```bash
pdf2jc run --pdf input/paper.pdf --figures-dir input/figs --output-dir output
```

这一步会：

1. 从 PDF 提取文字。
2. 标准化你手动提供的完整 Figure 图片。
3. 优先利用大写 panel 标签（A、B、C 等）检测 panel 区域。
4. 生成 panel 图片、边界框和 debug 图。

随后运行 panel 诊断：

```bash
pdf2jc diagnose-panels --pdf input/paper.pdf --figures-dir input/figs --output-dir output
```

重点检查：

```text
output/panel_detection_report.md
output/debug/Fig1_detected_labels.png
output/debug/Fig1_panel_regions.png
output/debug/Fig1_row_clusters.png
output/figures/Fig1A.png
```

所有 Figure 都应检查对应的 `Fig2`、`Fig3` 等文件。若报告中出现 `needs_manual_review: true`，应先检查 debug 图片，确认 panel 没有被漏检或错误切分。

## 五、生成并检查 Citation Mapping

运行：

```bash
pdf2jc diagnose-citations --output-dir output
```

这一步会识别正文中的引用，例如：

```text
Fig. 1A
Fig. 1A-C
Fig. 2D and E
Figs. 2A and 3B
```

并把它们映射到 `Fig1A`、`Fig1B` 等 panel 图片。

查看：

```text
output/citation_map.json
output/citation_mapping_report.md
output/citation_qc_table.html
```

请在浏览器中打开 `output/citation_qc_table.html`。逐项检查原始论文句子、识别出的 Figure 引用和显示的 panel 缩略图是否真的对应。

如需只重新导出 Citation QC 文件，运行：

```bash
pdf2jc export-citation-qc --output-dir output
```

## 六、生成 Narrative Units、Evidence Units 和 Slide Objects

运行：

```bash
pdf2jc diagnose-evidence-units --output-dir output
```

这一步会：

1. 按 Results 小标题和段落建立 Narrative Units。
2. 在同一段落中，根据 citation sentence 组织 Evidence Units。
3. 为每个 Evidence Unit 提议一个 Slide Object。

输出：

```text
output/narrative_units.json
output/evidence_units.json
output/slides.json
output/slide_review.html
output/slide_review.csv
```

请重点打开：

```text
output/slide_review.html
```

检查每个 Slide Object 的 panel 分组、支持原文句子和实验叙事。当前默认是 `sentence_grouped`：同一句引用的多个 panel 可以在一张 slide 中，但不同段落的 panel 不应自动合并。

## 七、生成最终可编辑 PowerPoint

在完成前面的 QC 后，运行：

```bash
pdf2jc build-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
```

成功后最终文件位于：

```text
output/jc_draft.pptx
```

同时会生成：

```text
output/presentation.json
output/theme_preview.html
```

如需确认生成结果，运行：

```bash
pdf2jc diagnose-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
```

## 八、推荐的完整命令顺序

每次处理一篇新论文，按以下顺序运行：

```bash
pdf2jc run --pdf input/paper.pdf --figures-dir input/figs --output-dir output
pdf2jc diagnose-panels --pdf input/paper.pdf --figures-dir input/figs --output-dir output
pdf2jc diagnose-citations --output-dir output
pdf2jc diagnose-evidence-units --output-dir output
pdf2jc build-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
pdf2jc diagnose-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
```

## 九、输出文件速查

| 目的 | 文件或文件夹 |
| --- | --- |
| 提取的论文文字 | `output/article_text.json` |
| 标准化 Figure | `output/manual_figures/` |
| 检测出的 panel 图片 | `output/figures/` |
| Panel debug 图 | `output/debug/` |
| Panel 检测报告 | `output/panel_detection_report.md` |
| Citation 映射 QC | `output/citation_qc_table.html` |
| Slide Object QC | `output/slide_review.html` |
| 最终可编辑 PPT | `output/jc_draft.pptx` |

## 十、当前版本限制

- Figure 需要用户手动保存，系统不会从 PDF 自动提取完整 Figure。
- Panel Detection 是自动初筛；任何 `needs_manual_review` 都应由使用者确认。
- Whole-figure 引用（例如 `Fig. 1`，没有指定 A/B/C）可能无法映射到特定 panel。
- `paragraph_grouped` 模式需要预先生成 `output/slides.paragraph_grouped.json`，目前不是默认的一键工作流。
- 当前公开版本的最终 PPT renderer 依赖特定 presentation artifact runtime，尚未完全打包为普通 Python 环境可独立安装的依赖。
- 当前 `run` 命令会在完成时清理 `output/pages/`；如果你需要永久保留 PDF 页面的 PNG，应在下一版本修复该行为。
