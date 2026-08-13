# Stage 7-11 轻量回溯训练

这份训练材料用于把已经完成的 Stage 7-11 重新装进“调用链”容器。每个阶段只做三件事：追一条真实链路、做一个小范围代码或测试修改、定位一次故障。目标是能定位和解释，不是背文件名或手写全部项目。

## 总规则

- 每阶段先看代码导航地图，再按“入口 → Service → 数据库/模型 → 输出 → 测试”阅读。
- A 级掌握主骨架和异常边界；B 级能读懂并小改；C 级知道用途并能定位即可。
- 每阶段只精读下方列出的必读代码，其余按概念搜索词定位。
- 用户的 `docs/learning/study_note.md` 只作为旁批，不纳入本训练文档的生产修改。

## Stage 7：上传可靠性

阅读顺序：`backend/tests/test_upload_documents.py` 的成功上传和伪造 DOCX 用例 → `backend/app/api/routes/documents.py` 的 `upload_document` → `backend/app/services/document_validation.py` 的 `validate_upload_metadata`/`validate_stored_content` → `backend/app/services/storage.py` 的 `LocalStorage.save` → `backend/app/models/document.py` 的哈希约束。

主调用链：`POST documents` → 确认知识库 → 校验声明元数据 → 分块落盘并计算 SHA-256 → 校验真实结构 → 应用层查重 → 数据库唯一约束提交 → 返回安全元数据；任一步失败都清理物理文件并回滚事务。

A级：能解释上传入口、真实内容校验、数据库提交和补偿边界。B级：能修改一个格式校验或清理断言。C级：能定位 Schema、迁移和测试 fixture。

必读代码：`backend/app/api/routes/documents.py`、`backend/app/services/document_validation.py`、`backend/app/services/storage.py`、`backend/tests/test_upload_documents.py`。

可跳过代码：PDF/DOCX fixture 的格式构造、依赖注入类型细节、Alembic 异步桥接。

本轮小修改：为完整上传 API 增加“ZIP 伪装 DOCX”测试，验证返回 422、未写入 Document、正式目录和临时目录均为空。

故障定位练习：如果该测试返回 201，沿 `upload_document` → `validate_stored_content` → `_validate_docx` → `test_document_validation.py` 断言定位；如果返回 422 但文件残留，沿 `storage.save` → 路由总 `except` → `LocalStorage.delete` 定位。

验收问题：

1. 为什么扩展名和 MIME 都合法，仍然必须打开 ZIP 检查 DOCX 骨架？
2. 并发重复上传时，哪个数据库约束是最终防线？冲突后为什么必须删除本次物理文件？
3. 删除流程为什么先 quarantine，再提交数据库？

## Stage 8：解析与切片

阅读顺序：`backend/tests/test_document_processing.py` → `backend/app/api/routes/documents.py` 的处理入口 → `backend/app/services/document_processing.py` → `backend/app/services/parsing/` → `backend/app/services/chunking.py` → `DocumentChunk`。

主调用链：处理入口 → 锁定文档并标记 processing → Registry 按格式解析 → Chunker 生成带来源位置的 drafts → 删除旧 chunks 后批量写入 → ready/partial/failed 提交。

A级：状态和重处理替换；B级：页码、标题路径、OCR 警告和滑窗；C级：解析契约和 fixture。

必读代码：`backend/app/api/routes/documents.py` 的 `process_document`/`_get_document_for_processing`、`backend/app/services/document_processing.py`、`backend/tests/test_document_processing.py`、`backend/tests/test_document_stage8_api.py`。

可跳过代码：PDF/XObject 的库细节、DOCX XML 命名空间、评测 JSON 的序列化细节。

本轮小修改：为处理 API 增加“文档不属于路径中的知识库”测试。它必须返回 404，解析 Service 不得被调用，并确认联合查询仍携带 `FOR UPDATE` 行锁。

故障定位练习：如果跨知识库请求仍开始解析，沿 `process_document` → `_get_document_for_processing` 的两个 `where` 条件 → `test_process_endpoint_rejects_document_outside_knowledge_base` 定位；如果重处理后有重复 chunks，沿 `DocumentProcessingService.process` → `_replace_chunks` → `DocumentChunk` 的 `(document_id, ordinal)` 唯一索引定位。

验收问题：

1. 为什么 `Document.status=ready` 不表示 OCR 已完成？
2. `FOR UPDATE` 行锁和 `(document_id, ordinal)` 唯一索引分别解决什么并发问题？
3. 为什么重处理必须先删除旧 chunks，再写新 chunks？

## Stage 9：向量化与 dense 检索

阅读顺序：`tests/test_embedding_services.py` → `documents.py` 向量入口 → `backend/app/services/embeddings/indexing.py` → Provider 契约 → `DocumentChunk` embedding 字段。

主调用链：检查文档归属与 ready → 找 stale chunks → 用固定 provider/model/dimension 向量化 → 按 content hash 幂等写入 → 返回新增、更新、跳过统计。

A级：ready、模型一致性和幂等；B级：批量向量化和失败边界；C级：pgvector 列声明。

必读代码：`backend/app/services/embeddings/indexing.py`、`backend/app/services/embeddings/contracts.py`、`backend/tests/test_embedding_services.py`、`backend/app/api/routes/documents.py` 的 embeddings 入口。

可跳过代码：Sentence Transformer 加载细节、HNSW DDL 参数和本地模型下载脚本。

本轮小修改：增加“同批次一条向量仍有效、另一条 content hash 已变化”的测试，验证只向 Provider 发送过时切片，并返回 `embedded_chunks=1/skipped_chunks=1`。

故障定位练习：如果内容已变但仍被跳过，沿 embeddings 路由 → `index_document` 的 `stale_chunks` 条件 → `embedding_content_hash` 与 `content_hash` 断言定位；如果 Provider 收到全部 chunks，检查 stale 列表是否在批处理前生成。

验收问题：

1. 为什么 provider、model、dimension 和 content hash 必须一起记录？
2. 为什么 embedding 服务不能靠向量相似度判断日期或权限？
3. Provider 在第二批失败时，为什么当前实现不提交第一批的半成品？

## Stage 10：关键词与混合检索

阅读顺序：`tests/test_stage10_retrieval.py` → `routes/retrieval.py` → `services/retrieval.py` → dense/keyword 两路 → RRF 与文档多样性。

主调用链：身份范围与费用日期进入检索 → 两路各自硬过滤 → 召回 → RRF 融合 → 去重与多文档多样性 → 返回带版本和来源的 top-k。

A级：过滤必须早于融合；B级：RRF 和多样性代价；C级：评测 JSON。

必读代码：`backend/app/services/retrieval.py`、`backend/app/api/routes/retrieval.py`、`backend/tests/test_stage10_retrieval.py`。

可跳过代码：PostgreSQL 全文检索实现细节、RRF 常量的调参过程、基线报告 JSON 全量字段。

本轮小修改：增加空 `allowed_scopes` 测试，验证 Hybrid 直接返回空结果，dense 和 keyword 两条召回路径均不调用；没有获授权范围时不应先召回再过滤或融合。

故障定位练习：若越权或过期切片进入 RRF，沿 `routes/retrieval.py` 的身份范围来源 → `HybridRetrievalService.search` 的参数透传 → `DenseRetrievalService` 与 `KeywordRetrievalService` 各自的 `_document_scope_filters` 定位；不要只检查 RRF，因为污染在融合之前发生。

验收问题：

1. 为什么 dense 与 keyword 路径都要独立执行日期和权限过滤？
2. 为什么 RRF 不能直接相加 dense similarity 与 keyword score？
3. 文档多样性先选代表切片会牺牲什么，又避免什么？

## Stage 11：引用回答与评测

阅读顺序：`tests/test_stage11_answering.py` → `routes/answers.py` → `services/answering.py` → `answer_prompts.py`/Provider → `evaluation/answer_metrics.py`。

主调用链：缺日期直接追问；有日期先 hybrid 硬过滤；无证据直接拒答；有证据才调用百炼；服务端校验引用白名单并映射真实 chunk；领域硬指标先于 RAGAS。

A级：回答 Service 主骨架和安全边界；B级：Prompt/Provider/引用评测；C级：Schema 和 runner 样板。

必读代码：`backend/app/services/answering.py`、`backend/app/api/routes/answers.py`、`backend/app/services/answer_prompts.py`、`backend/tests/test_stage11_answering.py`。

可跳过代码：LangChain 消息对象、百炼 SDK 重试细节、RAGAS runner 的并发和序列化样板。

本轮小修改：增加公共回答 API 的不可信引用测试，验证模型返回不存在的 `S9` 时服务端返回 502，而不是把它映射成真实引用或 200 正常答案。

故障定位练习：如果虚构来源进入响应，沿 `answers.py` → `AnswerService.answer` 的 `evidence_by_id/unknown_ids` → `test_answer_rejects_citation_not_present_in_retrieved_context` 和本轮 API 测试定位；如果权限或日期切片进入模型上下文，回到 Stage 10 的两路 SQL 过滤，不要只改 Prompt。

验收问题：

1. 引用白名单能证明什么，为什么不能证明答案要点完整或事实正确？
2. 为什么缺日期和无证据时不应调用回答模型？
3. 为什么检索上下文必须被 Prompt 标记为不可信数据，但这仍不能替代服务端过滤？

## 完成标准

完成每阶段后，至少能回答三个验收问题，并能用概念搜索词在 1-2 分钟内定位“入口 → Service → 查询条件 → 测试”。Stage 7-11 回溯完成后，再进入 Stage 12 Function Calling。
