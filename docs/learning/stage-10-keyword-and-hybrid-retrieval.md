# Stage 10：中文关键词检索与文档感知 hybrid 融合

## 当前状态

Stage 10 工程实现、正式对照、代码阅读和学习验收均已完成，40 道评测题均经项目负责人逐题确认：

- 使用应用侧确定性中文领域词 + 双字切分，记录 tokenizer 版本。
- 分词结果写入 PostgreSQL 文本字段，并用 `simple` 配置的 GIN 全文索引检索。
- keyword 与 dense 共用知识库、费用日期、访问范围和文档状态硬过滤。
- 使用 RRF 融合两个排名，不直接相加 cosine similarity 与 `ts_rank`。
- 使用文档感知融合，先给高分文档 3 个多样性名额，再补充高分切片，避免同一通知的多个切片挤掉另一份必要制度。
- 如果同一 `policy_type` 的多个有效版本同时进入结果，API 显式返回 `version_conflicts`，不静默混合。
- 40 题正式对照中，dense 与 hybrid 均 40/40 通过；keyword 39/40，通过率 `0.975`。
- 文档版本级 MRR：dense `0.9167`、keyword `0.9211`、hybrid `0.9298`。
- 三种策略的权限/禁止版本过滤准确率均为 `1.0`。

这些数字只针对 6 篇虚构制度、30 个 chunks 和 40 道小型离线题。它们不是回答准确率，也不能证明生产流量表现。

## 三条主链路

```text
POST /documents/{document_id}/keyword-index
  -> 校验文档属于当前知识库且 status=ready
  -> 查询全部 chunks
  -> tokenizer + content_hash 未变化则跳过
  -> 对过期 chunks 做确定性中文分词
  -> 写入 keyword_search_text 与追踪元数据
  -> 全部成功后一次 commit
```

```text
POST /knowledge-bases/{knowledge_base_id}/search/keyword
  -> 公共路由固定 all_employees
  -> 应用侧使用同一 tokenizer 处理 Query
  -> SQL 先过滤知识库、日期、权限和 ready 状态
  -> PostgreSQL to_tsvector + websearch_to_tsquery 召回
  -> ts_rank_cd 排序并返回可追溯 chunks
```

```text
POST /knowledge-bases/{knowledge_base_id}/search/hybrid
  -> dense 和 keyword 分别在硬过滤后召回候选
  -> 将两个通道的名次转换为 1 / (60 + rank)
  -> 同一 chunk 的 RRF 分数相加
  -> 同一文档在两个通道的最佳名次形成文档级 RRF
  -> 前 3 个名额做文档多样化，剩余名额按切片 RRF 补齐
  -> 返回 dense/keyword 名次、两级 RRF 分数和版本冲突
```

## 阅读顺序

### 第一遍：先看完整业务链路（A级）

1. `backend/tests/test_stage10_retrieval.py` 中的 keyword SQL 与 hybrid 测试。
2. `backend/app/api/routes/retrieval.py` 中的 `/keyword`、`/hybrid` 路由。
3. `backend/app/services/retrieval.py` 中的 `KeywordRetrievalService` 和 `HybridRetrievalService`。

只要求讲清：入口收到什么、Service 做哪两个检索、硬过滤在哪里、融合后输出什么。无需背函数名。

### 第二遍：索引与幂等（A级）

1. `backend/app/services/keyword_indexing.py`
2. `backend/app/services/keywording.py`
3. `backend/app/models/document_chunk.py` 中 `keyword_*` 字段。

重点理解：tokenizer 版本和 `keyword_content_hash` 为什么决定是否重建；任一 chunk 失败时为什么不能留下半份新索引。

### 第三遍：融合与评测（B级）

1. `backend/app/services/retrieval.py` 中 RRF 和文档多样化部分。
2. `backend/app/evaluation/retrieval_metrics.py`。
3. `data/evaluation/results/stage10_retrieval_comparison.json` 只看 `strategies.*.aggregate` 和失败题。

重点理解：原始向量相似度和关键词分数不在同一尺度；RRF 比较名次。当前 keyword 的失败题是 `DENSE-EVAL-004`：“普通城市”与原文“其他城市”并非字面匹配，说明关键词检索不能替代语义检索。

## 分级要求

- A级：路由、关键词索引 Service、keyword/hybrid 主骨架、知识库/日期/权限过滤和异常边界。能写伪代码或约七成骨架。
- B级：中文分词、PostgreSQL FTS、RRF、文档多样化、MRR 与版本正确率。能读懂、解释和修改。
- C级：Schema、Alembic、baseline 临时数据导入和 JSON 序列化。知道用途并能定位即可。

## 必读与可跳过

必读：

1. `backend/app/services/retrieval.py`
2. `backend/app/services/keyword_indexing.py`
3. `backend/app/api/routes/retrieval.py`
4. `backend/tests/test_stage10_retrieval.py`

可跳过：

- `backend/alembic/versions/d9f2a6b7c8e1_add_keyword_search.py` 的 DDL 细节。
- `backend/app/schemas/retrieval.py` 的字段声明细节。
- `backend/app/evaluation/stage10_baseline.py` 的临时文件、环境版本和 JSON 输出样板。
- Stage 7/8 已通过的上传、解析、切片测试，无需重新精读。

## 新增 10 题确认记录

31. 上海每人每晚 600 元住宿费上限 -> `TRAVEL-2026.1`
32. 目的地为上海、参加公司指定大型展会且已关联展会清单时，住宿费最高标准 -> `HOTEL-SUP-2026.1`
33. 返程后 15 个自然日内提交差旅报销 -> `REIMB-2026.1`
34. 电子发票原始电子文件不能只传聊天截图 -> `INVOICE-2026.1`
35. 高铁动车二等座报销标准 -> `TRAVEL-2026.1`
36. 原始票据遗失需要付款记录和替代材料 -> `REIMB-2026.1`
37. 财务角色查询 2000 元加强复核阈值 -> `AUDIT-2026.1`
38. 普通员工查询同一内部阈值 -> 必须排除 `AUDIT-2026.1`
39. 2025 年省会城市每晚 400 元 -> `TRAVEL-2025.1`，排除 `TRAVEL-2026.1`
40. 住宿报销需要发票、酒店账单和付款凭证 -> `INVOICE-2026.1` + `REIMB-2026.1`

## 验收问题

1. 为什么 keyword 和 dense 必须各自在融合前执行知识库、日期和权限过滤，而不能先召回再统一删除？
2. 为什么不能把 cosine similarity 与 `ts_rank` 直接相加？RRF 解决了什么，又引入了什么信息损失？
3. 为什么 chunk 级 RRF 会漏掉跨文档答案？文档多样化怎样修复，代价是什么？

## 当前验证命令

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m alembic check
```

40 题数据集已标记为 `reviewed`，正式 runner 未使用 `--allow-draft`，正式报告的 `formal_baseline=true`。三道验收题已通过：能够解释融合前硬过滤、RRF 的尺度处理与信息损失，以及文档多样化解决跨文档召回的方式和代价。Stage 10 已达到独立提交条件。
