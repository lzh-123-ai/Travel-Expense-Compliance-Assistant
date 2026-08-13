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

## Stage 9：向量化与 dense 检索

阅读顺序：`tests/test_embedding_services.py` → `documents.py` 向量入口 → `backend/app/services/embeddings/indexing.py` → Provider 契约 → `DocumentChunk` embedding 字段。

主调用链：检查文档归属与 ready → 找 stale chunks → 用固定 provider/model/dimension 向量化 → 按 content hash 幂等写入 → 返回新增、更新、跳过统计。

A级：ready、模型一致性和幂等；B级：批量向量化和失败边界；C级：pgvector 列声明。

## Stage 10：关键词与混合检索

阅读顺序：`tests/test_stage10_retrieval.py` → `routes/retrieval.py` → `services/retrieval.py` → dense/keyword 两路 → RRF 与文档多样性。

主调用链：身份范围与费用日期进入检索 → 两路各自硬过滤 → 召回 → RRF 融合 → 去重与多文档多样性 → 返回带版本和来源的 top-k。

A级：过滤必须早于融合；B级：RRF 和多样性代价；C级：评测 JSON。

## Stage 11：引用回答与评测

阅读顺序：`tests/test_stage11_answering.py` → `routes/answers.py` → `services/answering.py` → `answer_prompts.py`/Provider → `evaluation/answer_metrics.py`。

主调用链：缺日期直接追问；有日期先 hybrid 硬过滤；无证据直接拒答；有证据才调用百炼；服务端校验引用白名单并映射真实 chunk；领域硬指标先于 RAGAS。

A级：回答 Service 主骨架和安全边界；B级：Prompt/Provider/引用评测；C级：Schema 和 runner 样板。

## 完成标准

完成每阶段后，至少能回答三个验收问题，并能用概念搜索词在 1-2 分钟内定位“入口 → Service → 查询条件 → 测试”。Stage 7-11 回溯完成后，再进入 Stage 12 Function Calling。
