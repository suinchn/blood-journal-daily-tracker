# blood-journal-daily-tracker

23 本血液/肿瘤学核心期刊每日追踪技能。

从 PubMed 抓取指定日期（默认最近 1 天）内 23 本期刊的文章标题与摘要，完整翻译为中文（保留全部量化数据），生成「血液朝夕录」格式日报，本地保存后可上传到 IMA 知识库。

## 能力
- PubMed 按 NLM 期刊缩写检索 23 本血液/肿瘤学期刊（纯血液期刊全量保留，综合/肿瘤期刊按关键词过滤剔除纯实体瘤内容）
- 同查 pdat/edat 两种日期字段，兼容日期源差异
- 支持指定日期补抓（带去重关闭）与最近 N 天窗口
- 强信号加权的临床/实验分类
- 自动生成 Markdown 中文日报

## 组成
- `SKILL.md` — 技能说明与执行流程
- `scripts/fetch_23journals.py` — PubMed 抓取与日报初稿生成
- `scripts/upload_blood_daily.cjs` — 上传日报到 IMA 知识库

## 依赖 / 凭证
- Python 3.11+；Node 14+（上传脚本）
- NLM API Key：环境变量 `NLM_API_KEY` 或 `~/.config/nlm/api_key`
- IMA 凭证（仅上传需要）：环境变量 `IMA_OPENAPI_CLIENTID`/`IMA_OPENAPI_APIKEY` 或 `~/.config/ima/{client_id,api_key}`

## 用法
```
python scripts/fetch_23journals.py                      # 抓取最近 1 天（带去重，cron 用）
python scripts/fetch_23journals.py --date 2026-08-09    # 抓取指定日期
python scripts/fetch_23journals.py --date 2026-08-09 --days 3   # 指定日期往前 N 天
```
