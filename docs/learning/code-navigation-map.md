# 代码导航地图：Stage 7–11

这份地图不是函数背诵表。定位时只记三件事：**职责位置、概念搜索词、调用方向**。目标是在 1–2 分钟内沿“接口 → Service → 查询/模型 → 测试”找到改动点。

## 建议阅读顺序

1. 先看本页的五条主链路，只建立全局容器。
2. 当前学习哪个阶段，再打开对应 `stage-XX-*.md`。
3. 先读 A 级入口和 Service 主骨架，再读一份必读测试。
4. B 级实现按问题进入；C 级样板只在报错或改数据结构时定位。

## 五条主链路

| 阶段 | 主调用链 | 主要输出 | 概念搜索词 |
| --- | --- | --- | --- |
| Stage 7 上传 | `documents.py` 上传入口 → 文件校验/Storage → `Document` → commit/补偿 | 文档元数据与物理文件 | `upload_document`、`validate`、`storage.save`、`IntegrityError` |
| Stage 8 解析 | `documents.py` 处理入口 → `DocumentProcessingService` → Parser Registry → Chunker → `DocumentChunk` | ready/failed、切片、解析警告 | `process_document`、`registry.parse`、`chunker.chunk`、`_replace_chunks` |
| Stage 9 向量化 | `documents.py` 向量入口 → `DocumentEmbeddingService` → Embedding Provider → chunk embedding 字段 | 新增/跳过向量数量 | `embed_document`、`index_document`、`stale_chunks`、`content_hash` |
| Stage 10 检索 | `retrieval.py` hybrid 入口 → `HybridRetrievalService` → dense + keyword → RRF/文档多样性 | top-k chunks、版本冲突 | `hybrid_search`、`allowed_scopes`、`expense_date`、`rrf_score` |
| Stage 11 回答 | `answers.py` → `AnswerService` → hybrid 检索 → Prompt/百炼 Provider → 引用白名单 | answered/refused/追问、引用、警告 | `answer_question`、`expense_date is None`、`unknown_ids`、`generate_answer` |

## A/B/C 阅读级别

### A 级：必须能讲主骨架

- `backend/app/api/routes/documents.py`：上传、处理、向量化三个入口的请求编排。
- `backend/app/api/routes/retrieval.py`：dense/keyword/hybrid 的公共入口。
- `backend/app/api/routes/answers.py`：问答入口和当前权限传递位置。
- `backend/app/services/document_processing.py`：解析、切片、替换旧 chunks、状态提交。
- `backend/app/services/embeddings/indexing.py`：ready 检查、过时向量判断、批量更新。
- `backend/app/services/retrieval.py`：权限/日期过滤、双路召回、RRF 和文档多样性。
- `backend/app/services/answering.py`：确定性追问/拒答、模型调用、引用白名单。

合格标准：能用伪代码讲清输入、核心判断、数据库或模型输出、失败后果；不要求背准确函数名。

### B 级：需要能读懂和小改

- `backend/app/services/parsing/`：格式解析、页码/标题路径、图片与 OCR 警告。
- `backend/app/services/chunking.py`：结构边界、滑窗和确定性 content hash。
- `backend/app/services/embeddings/sentence_transformer.py`：本地 BGE 适配。
- `backend/app/services/keywording.py` 与 `keyword_indexing.py`：中文关键词索引。
- `backend/app/services/answer_prompts.py` 与 `bailian_answer_provider.py`：Prompt 版本和百炼适配边界。
- `backend/app/evaluation/`：领域指标、Prompt 对照和 RAGAS 辅助评判。

### C 级：知道用途并能定位

- `backend/app/schemas/`：HTTP 输入输出字段。
- `backend/app/models/`：ORM 列、索引和表关系。
- `backend/alembic/versions/`：数据库结构变更历史。
- fixture、manifest 导入、报告序列化和框架内部实现。

## 必读测试

每阶段只精读一份业务测试，其余按故障搜索：

| 阶段 | 必读测试 | 看什么 |
| --- | --- | --- |
| 7 | `tests/test_upload_documents.py`、`test_manage_documents.py` 中补偿用例 | 并发去重与跨存储恢复 |
| 8 | `tests/test_document_processing.py` | 重处理替换、无文本失败、partial/OCR |
| 9 | `tests/test_embedding_services.py` | 幂等、内容变更、Provider/维度校验 |
| 10 | `tests/test_stage10_retrieval.py` | 权限/日期过滤、RRF、多文档召回 |
| 11 | `tests/test_stage11_answering.py` | 缺日期、无证据、虚构引用、评测证据一致性 |

其余测试无需逐文件背诵。遇到故障先用 `rg -n "关键词" backend/app backend/tests` 找调用和断言。

## 三类故障怎么定位

### 权限或日期过滤失效

```text
search/answer 接口
  → allowed_scopes / expense_date 从哪里来
  → retrieval.py 的查询过滤条件
  → test_stage10_retrieval.py 的边界断言
```

### 文档重处理后出现重复 chunks

```text
documents process 接口
  → DocumentProcessingService.process
  → _replace_chunks 是否先删除旧数据
  → test_document_processing.py 的 reprocessing 用例
```

### 模型引用了不存在的来源

```text
answers 接口
  → AnswerService.answer
  → evidence_by_id / unknown_ids 白名单
  → test_stage11_answering.py 的虚构引用用例
```

## 当前验收问题

1. 用户问答时，日期和权限分别在哪一层进入检索，为什么 Prompt 不能替代数据库过滤？
2. 文档内容变化后，向量化服务依据什么字段只更新过时 chunks？
3. 收到“模型引用 S9，但本次只提供 S1–S5”的故障，你会沿哪四个位置定位？
