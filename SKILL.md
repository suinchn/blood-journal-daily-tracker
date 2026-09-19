---
name: blood-journal-daily-tracker
description: >
  23本血液/肿瘤学期刊指定日期追踪技能。从 PubMed 抓取指定日期（默认最近1天）的期刊文章及摘要，
  将标题与摘要完整翻译为中文（保留全部量化数据），生成"血液朝夕录"格式日报，
  本地保存后通过 ima-skill 上传到 IMA 知识库「血液笔记」→「血液日报」文件夹。
  当用户提到"血液期刊追踪"、"血液日报"、"23本期刊"、"今日血液文献"或要求抓取指定日期的期刊文章时使用。
---

# 23本血液/肿瘤学期刊每日追踪 (blood-journal-daily-tracker)

## 触发条件
- 用户要求执行"血液期刊追踪"、"血液日报"、"抓取血液文献"（可指定日期）
- 定时任务引用本 skill 自动运行

## 工作目录与脚本
| 文件 | 用途 |
|------|------|
| `scripts/fetch_23journals.py` | **唯一抓取脚本**（PubMed 抓取，23本期刊，支持 `--date` 指定日期，pdat+edat 双日期字段，itertext 完整提取标题/摘要（含 `<b>/<i>` 子标签），缺失摘要自动逐篇补抓，**血液相关性过滤**（纯血液期刊全量保留、综合/肿瘤期刊剔除实体瘤），输出含 rating/study_type/fulltext_status 与 excluded 明细） |
| `scripts/fetched_articles.json` | 去重数据库（cron 默认模式按它去重，勿删） |
| `scripts/upload_blood_daily.cjs` | IMA 上传（import_doc 创建笔记 → add_knowledge 关联「血液笔记」→「血液日报」） |
| `data/daily_report_YYYYMMDD.json` | 抓取的原始结构化数据（文章+英文摘要） |
| `data/daily_report_YYYYMMDD.md` | 初稿报告（关键词替换，[待翻译] 占位） |
| `data/daily_report_YYYYMMDD_formatted.md` | **最终中文报告**（AI 完整翻译后，上传用这份） |

## 常量（勿改）
- IMA 知识库「血液笔记」: `OrliMk9lo0HEPm-3k4qiDl803zUWG_K1HOcQiNK9lo4=`
- 「血液日报」文件夹: `folder_7490548709474198`
- 笔记标题格式: `🩸 血液朝夕录 - YYYY-MM-DD`
- Python: `<您的Python解释器绝对路径>`（Windows 下勿用 `python3`）
- NLM API Key（PubMed 接口）：从环境变量 `NLM_API_KEY` 或文件 `~/.config/nlm/api_key` 读取（勿改脚本内逻辑）

## 执行步骤

### 第一步：抓取（指定日期或默认最近1天）
```bash
PY=<您的Python解释器绝对路径>
# 指定日期（历史补抓，不去重）：
$PY ~/.claude/skills/blood-journal-daily-tracker/scripts/fetch_23journals.py --date 2026-08-09
# 默认最近1天（带去重，供 cron 使用）：
$PY ~/.claude/skills/blood-journal-daily-tracker/scripts/fetch_23journals.py
```
输出写入 `data/daily_report_YYYYMMDD.json`（结构化数据，含英文摘要）。

### 第二步：AI 中文化并生成最终报告 ⚠️核心 —— 用子 agent 翻译，防止主上下文过载

**原则：原文内容不进主上下文。** 不要直接把 `daily_report_YYYYMMDD.json` 读进主会话；由子 agent 读文件→翻译→写盘，主 agent 只接触路径、统计和校验结果。

1. **统计**：用 Python 一行读取 JSON，打印总篇数 / P0 / P1 / P2 / 临床 / 实验 / 全文 / 仅摘要计数（供概览和派发决策，输出到 kw 内即可）。
2. **派发**：单日 ≤40 篇 → 1 个 general-purpose 子 agent 全量翻译；单日 >40 篇 → 按报告三段并行派发（P0/P1 临床、P0/P1 实验、P2 表格），各子 agent 写片段文件后主 agent 拼接。
3. **子 agent prompt 模板**（`<YYYYMMDD>` 换成实际日期，两个路径都改为绝对路径）：
```bash
你是血液日报翻译执行 agent。核心职责：文件到文件，不要向主 agent 回传任何文章原文或译文内容。

1. Read <<本skill目录>/SKILL.md>：
   严格照做其中的「第二步」强制要求 1-6（完整中文翻译/保留量化数据/P2 表格每行一句话总结等）和「报告格式（血液朝夕录 v3）」模板。
2. Read <data/daily_report_<YYYYMMDD>.json>，逐篇翻译并生成最终中文报告。
2b. 该 JSON 顶层可能有 `excluded` 字段（当天被过滤的非血液相关文章明细）：在概览中体现剔除数量即可（`- **非血液相关已剔除**: N 篇`），**不要**翻译或输出这些文章。
3. Write（含多轮 tool call 分批、多次 Edit 追加）到
   <data/daily_report_<YYYYMMDD>_formatted.md>（UTF-8，**上传用这份**）。
4. 某篇摘要截断时按 SKILL.md 用 efetch 单篇补抓后再翻译。
5. 完成后只返回以下紧凑摘要（禁止粘贴原文/译文）：
   - 总篇数 / P0 / P1 / P2 各多少
   - 完整翻译摘要篇数、无摘要已给中文说明篇数
   - 输出文件大小（行数/字节）
   - 未翻译或异常文章的 PMID 列表（如有）
```
4. **子 agent 只回传紧凑摘要**（同上第 5 条），主 agent 不得要求它贴回翻译内容。
5. **校验（不可省，翻译是用户投诉重灾区）**：
   - 确认 `data/daily_report_<YYYYMMDD>_formatted.md` 存在且非空
   - `grep -E "\[待翻译\]|\[AI填写|\[AI补充|\[待AI"` 该文件 → 必须无残留
   - 抽查 1 篇 P0/P1：中文摘要完整、量化数据（HR/CI/P）齐全

以下为**翻译质量强制要求**（子 agent 通过 Read SKILL.md 获得并照做；主 agent 不要在会话里重复翻译全文）：

**强制要求（用户已多次投诉，不可跳过）：**
1. **标题**：完整中文医学翻译，首行中文，第二行斜体英文原标题。禁止中英混杂（如 ~~CAR-T细胞s~~）。
2. **摘要——所有文章必须有中文摘要**：
   - 有摘要的文章：完整中文翻译，禁止英文原文或中英混杂。保留所有量化数据（HR/CI/P-values/OS/PFS/ORR/样本量）。摘要含子标签被截断时，用 `efetch.fcgi?db=pubmed&id=<PMID>&retmode=xml` 单篇补抓或按 PMID 查原文补全后再翻译。
   - 补抓后仍无摘要的文章（勘误/回复信/评论/通讯等类型）：**必须给出中文说明**——翻译标题 + 标注文章类型（如"勘误/回复信/评论"）+ 基于标题和期刊信息的简短中文解读，不得留空或仅写"无摘要"。
3. **临床经验/研究启示总结**：必须具体、引用数据（药物名、方案、HR、P 值），禁止"值得关注""前景广阔"等空洞表述。
4. **分类**：临床研究（RCT/队列/回顾性/指南/真实世界等以患者为对象）与实验研究（机制/动物/细胞等）分两大板块；按 rating 分 P0(★★★★★)/P1(★★★★)/P2(★★★)。
5. 文章数>20 时优先保证 P0/P1 摘要完整翻译，P2 用表格浓缩。
6. **P2 及低优先级表格（强制）**：所有 P2/评论/勘误表格必须包含列：`中文标题 | 类型 | 一句话总结 | PMID | PubMed链接 | 全文状态`。**每行都必须有一句话总结**（基于标题+类型+可用信息的一句话中文解读），不得留空、不得只重复标题。勘误/回复信等类型必须标注（勘误/回复信/评论/综述/病例报告/临床观察/转化研究/基础研究等）。

### 报告格式（血液朝夕录 v3）
```markdown
# 🩸 血液朝夕录 — YYYY-MM-DD

## 📊 今日概览
- **获取期刊数**: X/23 ✅
- **新文章数**: X 篇（📖全文 X / 📄仅摘要 X）
- **临床研究**: X 篇 | **实验研究**: X 篇
- **非血液相关已剔除**: X 篇（如为 0 可省略）

---
## 🔬 第一部分：临床研究
### P0 — 重磅临床研究（★★★★★）
### N. [完整中文翻译标题]
*[英文原文标题]*

**来源**：期刊名 | PMID: xxxx ([PubMed](https://pubmed.ncbi.nlm.nih.gov/xxxx/)) | ★★★★★ | 📖全文/📄仅摘要 ⚠️
- **作者**: xxx et al.
- **发表日期**: YYYY-MM-DD

> **中文摘要**: [完整中文翻译，保留全部量化数据]
> **关键数据**: HR=xx (95% CI, P=xxx) | ORR=xx% | OS/PFS 中位数等

#### 📌 临床经验提升总结
> - [引用具体数据/药物/方案的临床启示]

---
## 🧪 第二部分：实验研究（基础科学）
#### 💡 研究启示总结
> - [引用分子靶点/通路/数据的启示]

---
## 📋 P2 — 其他值得关注（★★★）精选
| # | 期刊 | [中文标题] | 类型 | 一句话总结 | PMID | PubMed链接 | 全文状态 |
|---|------|-----------|------|-----------|------|------------|---------|

*本报告由血液期刊追踪 skill 自动生成于 YYYY-MM-DD | 数据来源: PubMed + PMC OA*

### 📌 今日快速提醒
[3条以内关键提醒]
```

### 第三步：上传到 IMA（使用 ima-skill）
使用已安装的 ima-skill（`~/.claude/skills/ima-skill/`，凭证在 `~/.config/ima/client_id` + `api_key`）：
1. `import_doc` 创建笔记（content_format=1, title=`🩸 血液朝夕录 - YYYY-MM-DD`）
2. `add_knowledge` 关联：media_type=11, note_info.content_id=note_id, knowledge_base_id=「血液笔记」, folder_id=「血液日报」

**推荐直接调用上传脚本**（内部即上述流程，已验证）：
```bash
node ~/.claude/skills/blood-journal-daily-tracker/scripts/upload_blood_daily.cjs 20260809
```
> 上传失败（如 code 200005 日限流）时：报告已本地保存，次日重试；不要重复创建笔记。

### 第四步：汇报摘要
输出统计：总文章数、各期刊数量、P0/P1 篇数、上传结果（note_id/media_id）。

## 注意事项
- **UTF-8**：所有写入必须 UTF-8；Node 脚本内字符串避免直接内嵌 emoji 字面量（Windows git-bash 会崩），用 `\u` 转义或从文件读取。
- **跨语言路径**：Python/Node 共享文件用 Windows 原生路径（`平台原生绝对路径`），勿用 `/tmp`。
- **连接超时**：IMA 基础地址固定 `https://ima.qq.com`；连接失败先 `curl --connect-timeout 10 https://ima.qq.com` 排查网络。
- **日期校验**：抓取后核对 `data/daily_report_YYYYMMDD.json` 的 `search_window`，若日期不对或文章数为 0，直接输出简短说明结束。
- **data/ 保留**：`data/daily_report_*.json/md` 会随时间累积，成功上传后可手动清理旧报告；`scripts/fetched_articles.json` 是去重数据库，**不要删除**。
- **血液相关性过滤**：抓取后只保留血液/血液肿瘤相关文章。纯血液期刊（Blood、Blood Adv、Am J Hematol、Br J Haematol、Haematologica、Leukemia、Lancet Haematol、Blood Cancer J、Bone Marrow Transplant、Transplant Cell Ther、Ann Hematol）**全量保留**；综合/肿瘤期刊（JCO、NEJM、Nat Cancer、Cancer Cell、CA、Mayo Clin Proc、J Transl Med、STTT、J Immunother Cancer、J Hematol Oncol、Exp Hematol Oncol、Lancet Reg Health West Pac）按标题+摘要关键词过滤，剔除纯实体瘤文章。剔除明细在 JSON 的 `excluded` 字段，概览显示数量。关键词表在 `fetch_23journals.py` 的 `HEMATOLOGY_KEYWORDS`/`SOLID_TUMOR_KEYWORDS`，需要增补时直接改脚本并重跑。
- **临床/实验分类**：`study_type` 采用强信号加权判定（2026-08-27 根治泛词误判）——RCT/phase/指南/meta-analysis/患者队列数（如 "104 CAR-T recipients"）等临床决定词优先；机制/动物/细胞类强信号 ≥2 条也可反超；单个强实验信号仅在无临床强信号时胜出。日常一般无需改动；若需微调，改 `fetch_23journals.py` 的 `DECISIVE_TRIAL_TERMS`/`CLINICAL_STRONG`/`EXPERIMENTAL_STRONG` 等列表。
- **脚本维护**：本 skill 只保留 `fetch_23journals.py` + `upload_blood_daily.cjs` 两个脚本，翻译由 AI（Claude）在第二步完成。不要重新引入重复的抓取/翻译脚本，避免 schema 漂移。
