# Stage 9：embedding、pgvector 与版本感知 dense 检索

## 当前阶段状态

Stage 9 工程验收已完成，当前等待代码阅读、自检题和独立提交：

- 已完成 embedding Provider 契约、Sentence Transformers 本地 Provider 和安全错误边界。
- 已完成 512 维 pgvector 字段、embedding 追踪元数据、HNSW 索引与 Alembic 迁移。
- 已完成 chunk 级幂等批量向量化；整批向量先计算并校验，全部成功后才一次提交。
- 已完成知识库、费用日期、访问范围、模型版本和内容哈希过滤的 dense 检索。
- 公共 API 固定使用 `all_employees`，不接受客户端伪造 `finance_reviewer` 角色。
- 已使用确定性测试向量走通真实 PostgreSQL 写入与过滤闭环。
- 30 题检索评测集已由项目负责人逐题确认并冻结为 `reviewed`；第 20 题已改为更自然的跨文档检索问题。
- 已从 ModelScope 下载 `AI-ModelScope/bge-small-zh-v1.5@master`，并校验权重 SHA-256 为 `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`。
- 已用可清理的临时知识库跑通 6 篇制度、30 个 chunks 和 30 题真实 BGE baseline。
- 文档版本级 macro Recall@5、完整命中率、禁止版本过滤准确率和整题通过率均为 `1.0`；P50/P95/最大检索延迟为 `31.840/56.112/65.183 ms`。

模型边界：本阶段的 BGE 只作为可替换的 embedding Provider。后续回答生成会按路线默认接入阿里云百炼；两者使用不同的接口、版本记录和失败口径，不能用回答模型的调用成功代替检索 baseline。

测试向量只证明 pgvector 链路和过滤规则可用；真实 BGE baseline 才证明当前 6 篇虚构制度和 30 题冻结集上的 dense 检索结果。该数据量很小，且尚未评测回答生成、引用正确率或生产流量，不能把四项 `1.0` 写成“系统准确率 100%”。

## 两条主链路

```text
POST /documents/{document_id}/embeddings
  → 校验文档属于当前知识库且 status=ready
  → 查询该文档全部 chunks
  → content_hash + provider + model + dimension 相同则跳过
  → 对过期 chunks 分批生成向量
  → 校验数量、维度、有限值和非零向量
  → 全部成功后写入 embedding 元数据并一次 commit
```

```text
POST /knowledge-bases/{knowledge_base_id}/search
  → 公共路由固定 all_employees 范围
  → Provider 生成查询向量
  → SQL 先限定 knowledge_base_id
  → 按 expense_date 过滤 effective_from/effective_to
  → 过滤 access_scope、provider、model、dimension、content_hash
  → pgvector cosine distance 排序并返回可追溯 chunks
```

## 推荐代码阅读顺序

### 第一遍：Provider 契约

1. `backend/tests/test_embedding_services.py`
2. `backend/app/services/embeddings/contracts.py`
3. `backend/app/services/embeddings/sentence_transformer.py`
4. `backend/app/services/embeddings/factory.py`

重点回答：为什么 Provider 必须暴露模型名和维度？为什么模型导入和加载要延迟到第一次真实调用？

### 第二遍：向量持久化与幂等

1. `backend/app/models/document_chunk.py`
2. `backend/alembic/versions/c8e4b7a1d2f0_add_dense_embeddings.py`
3. `backend/app/services/embeddings/indexing.py`
4. `backend/tests/test_document_chunk_model.py`

重点追踪 `embedding_content_hash`：同一内容和模型为什么可以跳过，正文或模型变化后为什么必须重算。

### 第三遍：dense 检索与安全过滤

1. `backend/tests/test_dense_retrieval.py`
2. `backend/app/services/retrieval.py`
3. `backend/app/api/routes/retrieval.py`
4. `backend/tests/test_stage9_api.py`

重点区分两类顺序：业务上必须先限定知识库、日期和权限；SQL 执行计划可以由 PostgreSQL 选择，但最终结果不能包含过滤范围外的文档。

### 第四遍：检索评测

1. `backend/app/evaluation/contracts.py` 中的 `RetrievalEvalCase`
2. `data/evaluation/stage9_retrieval_v0.json`
3. `backend/tests/test_retrieval_evaluation_dataset.py`

30 题已经逐题检查问题日期、允许范围、期望版本和禁止版本，并冻结为 `reviewed`。真实 BGE 已在该冻结集达到文档版本级 macro Recall@5 `1.0`；这仍不等于回答准确率或生产环境准确率。

### 第五遍：真实 baseline 与指标口径

1. `backend/tests/test_retrieval_metrics.py`
2. `backend/app/evaluation/retrieval_metrics.py`
3. `backend/app/evaluation/stage9_baseline.py`
4. `data/evaluation/results/stage9_bge_dense_baseline.json`

重点区分 `macro_recall_at_5`、`full_expected_set_accuracy`、`forbidden_filter_accuracy` 和 `case_pass_rate`。跨文档题缺一个版本时 Recall 可以是部分得分，但整题不能通过；权限题即使没有期望文档，也必须检查禁止版本没有泄露。

## 当前验证命令

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m pip install -e ".[embedding,embedding-download,dev]"
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -c "from modelscope import snapshot_download; print(snapshot_download('AI-ModelScope/bge-small-zh-v1.5', revision='master', cache_dir=r'D:\xuexi\projects\agent_project\.model_cache', ignore_file_pattern=['pytorch_model.bin']))"
..\.venv\Scripts\python.exe -m app.evaluation.stage9_baseline --model-path <MODEL_PATH>
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
```

## 自检题

1. 为什么不能只保存 `embedding`，还要保存 provider、model、dimension 和 content hash？
2. 为什么批次 1 成功、批次 2 失败时不能先提交批次 1？
3. 为什么文档 `status=ready` 不等于向量一定已经生成？
4. 为什么费用日期过滤不能交给 embedding 相似度决定？
5. 为什么公共搜索 API 不接收客户端传入的财务角色？
6. HNSW 索引存在为什么不等于 Recall@5 已经合格？
7. 测试向量数据库冒烟与真实 BGE baseline 分别能证明什么？
8. 第 16 题未生效通知与第 19 题财务内部 SOP 分别由哪一种过滤规则排除？

## 下一步

1. 按推荐顺序阅读 Stage 9 代码，并完成 8 道自检题。
2. 复述向量化、dense 检索和 baseline 三条链路，重点解释模型追踪、日期/权限过滤与指标口径。
3. 学习验收通过后创建 Stage 9 独立提交，再进入 Stage 10 的关键词与 hybrid 检索对照。
