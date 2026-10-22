# 招投标监控数据字典

> Schema v2｜2026-08-05。公告事实与项目生命周期分层；数据库为单一事实源。

## 核心原则

- `notice_type`：公告一级类型，回答“这是什么公告”。
- `notice_subtype`：公告二级类型，回答“具体是哪类公告”。
- `project_stage`：项目生命周期状态，回答“项目现在进行到哪一步”。
- `award` 是公告类型；`awarded` 是项目状态，两者不能混用。
- 候选公示不是最终中标；无开标时间不能推断项目已经结束。

## notices（公告事实表）

| 字段 | 类型 | 允许值/示例 | 说明 |
|---|---|---|---|
| id | TEXT | MD5 | 公告稳定 ID |
| site | TEXT | 都市集团 | 来源平台 |
| notice_type | TEXT | tender/award/intention/other | 一级公告类型，兼容旧系统 |
| notice_subtype | TEXT | 见下表 | 二级业务类型 |
| classification_confidence | REAL | 0–1 | 自动分类置信度；人工校正为 1 |
| classification_reason | TEXT | 标题命中：中标结果公示 | 分类依据 |
| classification_version | TEXT | 2.0.0 | 分类规则版本，人工校正追加 `+manual` |
| title/url | TEXT | — | 公告标题和原文地址 |
| publish_date | TEXT | YYYY-MM-DD | 发布日期 |
| crawl_time | TEXT | YYYY-MM-DD HH:mm:ss | 首次入库时间（2026-08-06 起更新不覆盖） |
| last_seen | TEXT | YYYY-MM-DD HH:mm:ss | 最近一次抓到时间（2026-08-06 新增，站点活跃度） |
| tenderer | TEXT | — | 发包人/采购单位 |
| amount_value | REAL | 元 | 预算金额数值 |
| bid_deadline | TEXT | YYYY-MM-DD HH:mm:ss | 开标时间 |
| winner | TEXT | — | 中标单位，仅结果类有意义 |
| winning_amount | TEXT | 元 | 中标金额 |
| std_district | TEXT | 盐南/经开/市级… | 标准区县 |
| proj_major_cat | TEXT | 市政工程 | 行业大类 |
| tags | TEXT(JSON) | ["盐南","ICT"] | 统一标签 |
| raw_json | TEXT(JSON) | — | 原始抓取记录备份 |

`raw_json` 可附加：`actual_detail_url`（真实详情路由）、`detail_attachments`（文件名/下载地址，仅记录不自动下载）、`detail_api_id/guid`（公开详情接口定位键）。

### notice_subtype

| 值 | 中文 | 一级类型 |
|---|---|---|
| tender_notice | 招标/采购公告 | tender |
| tender_reopen | 二次/三次/重新采购 | tender |
| award_candidate | 中标/成交候选人公示 | award |
| award_final | 最终中标/成交/中选结果 | award |
| intention | 采购意向/招标计划 | intention |
| correction | 更正/澄清/补充 | other |
| failed | 流标/废标/采购失败 | other |
| terminated | 项目终止 | other |
| suspended | 项目暂停 | other |
| contract | 合同公告 | other |
| unknown | 暂未识别 | other |

## projects（项目主表）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT | 项目稳定 ID |
| canonical_name/display_name | TEXT | 归一化项目名/展示名 |
| project_stage | TEXT | 项目当前状态 |
| stage_reason | TEXT | 状态判定原因 |
| first_notice_date/latest_notice_date | TEXT | 首次/最近公告日 |
| open_date | TEXT | 最新有效开标时间 |
| budget_value | REAL | 原预算（元） |
| winner/winning_amount | TEXT/REAL | 最终中标单位/金额 |
| link_confidence | TEXT | manual/high/medium/low/standalone/none |

### project_stage

| 值 | 页面名称 | 判定 |
|---|---|---|
| upcoming | 待开标 | 开标日在未来 |
| upcoming_unknown_time | 待开标·时间待确认 | 无开标时间且公告不超过 30 天 |
| opening_today | 今日开标 | 开标日为今天 |
| pending_result | 待跟踪结果 | 开标已过无结果，或无时间且公告超过 30 天 |
| candidate | 候选公示 | 有候选公示、无最终结果 |
| awarded | 历史中标 | 有最终中标/成交公告 |
| failed | 流标/废标 | 采购失败且无后续重新采购 |
| terminated | 已终止 | 存在终止公告 |
| suspended | 已暂停 | 存在暂停公告 |
| intention | 商机线索 | 仅有采购意向/计划 |
| correction | 更正/澄清 | 尚未归并到主招标公告 |
| contract | 合同公告 | 尚未归并到中标项目 |
| unknown | 待识别 | 无法确定项目状态 |

## project_notices（项目—公告关系表）

| 字段 | 说明 |
|---|---|
| project_id/notice_id | 项目和公告 ID |
| relation_role | tender_primary/tender_reopen/candidate/award_final/correction/failed/terminated… |
| match_method | exact/substring/similar/manual |
| match_score | 0–100 |
| match_confidence | manual/high/medium/low/none |
| match_reasons | 项目名、平台、采购单位、时间、区县等匹配依据 |
| is_manual | 是否人工指定 |

## 关联可信度

| 等级 | 分数 | 使用规则 |
|---|---:|---|
| manual | 100 | 人工确认，可展示确定性指标 |
| high | ≥85 | 自动高可信，可计算折扣率 |
| medium | 70–84 | 显示“可能关联/约”，可计算近似折扣率 |
| low | 60–69 | 待人工复核，不展示确定性折扣率 |
| none | <60/冲突 | 不关联，记录未匹配原因 |
| standalone | — | 只有中标公告，未找到原招标 |

## 项目数据输出口径（原 tender.json Schema v2；2026-09-29 起经 8002 API 输出，字段不变）

- `schema_version=2`
- `summary.total`：公告数；`summary.projects`：项目数。
- `projects[]`：驾驶舱主列表数据。
- `sites{}`：兼容保留的公告审计数据。
- `links[]`：已关联招标—中标链，含分数、可信度和理由。
- `unmatched_notices[]`：未匹配中标公告及原因。

## 人工校正

文件：`manual_overrides.json`。

- `notice_overrides`：键可用公告 ID 或 URL，指定 `notice_type`、`notice_subtype`、`reason`。
- `link_overrides`：键可用中标公告 ID 或 URL，指定 `tender_id` 或 `tender_url`、`reason`。
- 每次重建时人工校正优先于自动规则。
