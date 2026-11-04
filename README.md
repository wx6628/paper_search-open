# paper_search-open · 本地论文检索工作站

一个**跑在你自己电脑上**的论文检索与文献库工具：

- **离线 dblp 检索**：把 dblp 全量数据（数百万篇计算机领域论文）建成本地数据库，按标题检索不到 1 秒，不依赖网络、不会被网站拦截。
- **按“你认可的出处 + 年份”排序**：你定义 tier 1 / 2 / 3 的会议和期刊，结果按 层级 → 年份档 → 相关度 排列，新论文优先，经典论文也保留名额。
- **补上 dblp 还没收录的新论文**：从 OpenAlex、Crossref、OpenReview、arXiv 抓优先出处的最新论文，并标明“已发表 / 已录用 / 审稿中 / arXiv 自称录用”。
- **个人文献库 + 图形界面**：在浏览器里（仅本机 127.0.0.1）浏览、两层分组、星标、阅读 PDF、写笔记、建立论文关联、看关系图。
- **AI 助手协作**：用 Claude Code、Codex 等 AI 工具一句话完成“检索 → 筛选 → 入库 → 报告 → BibTeX”，还能批量精读论文生成结构化笔记、核验参考文献的真伪。

> 所有数据（文献库、PDF、笔记、API key）都只保存在你的电脑上。

## 快速开始

完整的小白教程见 **[docs/新手教程与扩展指南.md](docs/新手教程与扩展指南.md)**，下面是简版：

```bash
# 1. 安装依赖（Python 3.10+）
python -m pip install -r requirements.txt

# 2. 下载 dblp 数据包放到 data/dblp/（见教程第 4 步），然后构建本地库（约 10 分钟）
python -I ps/dblp_build.py --dump data/dblp/<下载的文件>.xml.gz --db data/dblp.sqlite

# 3.（可选）复制 .env.example 为 .env，填入免费的 API key / 邮箱

# 4. 打开图形界面：Windows 双击 打开论文库.bat；macOS/Linux 运行 ./start.sh
#    浏览器访问 http://127.0.0.1:8765
```

然后在项目目录里启动 AI 助手：

| AI 工具 | 用法 |
|---|---|
| Claude Code | `/find 小样本目标检测中的元学习方法`、`/queue`、`/bib 分组名` …… |
| Codex 等 | 自然语言：“执行 find：小样本目标检测中的元学习方法” |

## 命令一览

| 命令 | 作用 |
|---|---|
| `find <研究问题> [分组]` | 完整检索：本地 dblp（按层级、年份档）+ dblp 缺口，AI 筛选后入库，生成报告和 BibTeX |
| `fresh <关键词>` | 只查优先出处中 dblp 尚未收录的新论文 |
| `enrich <分组/编号>` | 补摘要、引用数、开放获取链接与 PDF |
| `queue` | 处理图形界面里排队的 AI 精读任务，笔记自动挂到论文上 |
| `verify <文件>` | 核验参考文献是否真实存在、信息是否正确，输出修正后的 BibTeX |
| `bib <分组/编号/starred>` | 导出 BibTeX（优先使用 dblp 规范条目） |
| `library` | 启动图形界面 |
| `venues <出处清单>` | 配置优先出处 |
| `dblp-update` | 构建 / 更新本地 dblp |

## 文档

| 文档 | 内容 |
|---|---|
| [docs/新手教程与扩展指南.md](docs/新手教程与扩展指南.md) | 从零安装到熟练使用；如何定制和扩展 |
| [docs/操作指南.md](docs/操作指南.md) | 全部功能的详细说明（图形界面左侧“📖 使用指南”也能看） |
| [docs/DESIGN.md](docs/DESIGN.md) | 架构与设计决策 |
| [AGENTS.md](AGENTS.md) | 给 AI 助手看的规则（所有 AI 工具通用） |
| [docs/workflows/](docs/workflows/) | 每个命令的具体步骤（AI 按它执行） |

## 使用守则

- 工作站**从不在线访问 dblp.org**（网站有人机验证），只使用离线数据包。
- 所有在线 API 都受 `config/api_limits.yaml` 限速和限额约束；遇到人机验证立即停用该数据源，不尝试绕过。
- 只自动下载**开放获取**的 PDF；付费论文请通过合法渠道自行下载后关联。

## 许可证

代码采用 [MIT License](LICENSE)。dblp 数据（`data/dblp/dblp.dtd` 及你下载的数据包）由 Schloss Dagstuhl 以 CC0 1.0 发布。
