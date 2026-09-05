# Enterprise Travel Reimbursement RAG Assistant

企业国内差旅报销制度合规助手。系统目标是根据费用发生日期、制度版本和用户权限，返回能够追溯到原文的办理依据；没有足够证据时拒绝下结论。

这是一个兼顾学习与秋招作品集的项目。当前先把文档摄取做可靠，再逐步完成结构化解析、版本感知检索、引用问答、权限过滤和离线评测，不用技术名词代替可验证结果。

## 当前进度

当前已进入 **Stage 17：受控 OCR 条件增强**。Stage 12 的制度问答/本人报销状态路由、最小 JWT 身份、只读工具和百炼 Function Calling 适配，Stage 13 的合规预检查闭环，以及 Stage 14 的观测、Compose、CI 和限界前端均已完成；Stage 15 已完成本地 BGE 四组检索对照，Stage 16 已完成候选版冻结，线上默认保持 hybrid+RRF。

已经具备：

- FastAPI 分层 API、环境配置、存活/数据库就绪检查。
- PostgreSQL 17 + pgvector Docker 环境。
- SQLAlchemy AsyncSession 与异步 Alembic 迁移。
- 知识库创建、列表和详情 API。
- PDF、DOCX、Markdown、UTF-8 TXT 上传。
- 扩展名、MIME 与文件结构联合校验，大小和 DOCX 解压上限。
- 原子文件落盘、相对 storage key、流式 SHA-256。
- 知识库内内容去重，并由数据库唯一索引兜住并发重复。
- 文档列表、详情和删除；删除提交失败时恢复已隔离文件。
- Parser Registry，以及 PDF、DOCX、Markdown、TXT 原生文本解析器。
- PDF 真实页码、DOCX 标题/表格顺序、图片计数、扫描页与提取完整性警告。
- 确定性结构切片和 `document_chunks` 追溯元数据；重处理幂等且同文档并发处理串行化。
- 制度版本、生效期、访问范围和替代关系元数据。
- 6 篇垂直制度样本、版本化 manifest，以及 20 题经项目负责人逐题人工复核的评测基线 v0。
- 可替换 embedding Provider、本地 BGE Provider、512 维 pgvector 字段、模型追踪元数据和 HNSW 索引。
- chunk 级幂等批量向量化，以及知识库、费用日期、访问范围和模型版本过滤的 dense 检索 API。
- 30 题经项目负责人逐题复核的 Stage 9 检索评测基线，覆盖版本、日期、权限、跨文档和范围边界。
- 可复现的真实 BGE baseline runner：临时导入、解析、向量化、30 题检索、指标报告和数据清理形成闭环。
- 确定性中文关键词索引、PostgreSQL GIN 全文检索，以及文档感知 RRF hybrid 检索。
- 40 题经项目负责人逐题确认的 dense/keyword/hybrid 正式同集对照，新增 MRR、版本正确率和失败案例记录。
- 固定回答状态、可追溯引用、缺日期追问、无证据拒答，以及部分文本提取和版本冲突警告。
- 阿里云百炼 OpenAI 兼容 Provider 的 LangChain 适配；Key 只从 `DASHSCOPE_API_KEY` 读取，测试不联网。
- Stage 12 统一助手入口：状态问题跳过制度日期门槛；身份只来自服务端 JWT；报销工具通过 `employee_id` SQL 条件和脱敏审计约束资源归属；百炼 `tool_calls` 经过服务端二次 schema 校验。
- Stage 13 合规预检查：读取本人报销事实，按费用日期复用制度检索，明确 `normal`、`not_found`、`tool_failed`、`evidence_insufficient` 四条分支，并输出事实、制度依据、系统建议和节点轨迹。
- Stage 14 轻量可观测性：请求中间件统一生成 `request_id`，记录 intent、tool、retrieval、model、compliance 阶段耗时、失败类型、token 和可配置费用估算；提供聚合指标和最近请求轨迹接口。
- Stage 14 可运行交付：Compose 同时编排 PostgreSQL、API、前端并等待健康检查；GitHub Actions 执行 Ruff/pytest；限界 React 页面覆盖制度问答、本人状态、合规预检查、引用和工具轨迹展示。
- Stage 15 受约束检索增强：保留原始问题、费用日期和服务端权限快照的确定性查询副本，候选集合并后的可替换 reranker，四组消融配置、NDCG@K 契约和本地 BGE 评测 runner；40 题正式本地对照已完成，硬过滤泄漏为 0，线上默认仍回退 hybrid+RRF。
- Stage 16 已冻结 72 道候选版回归题的题集来源、内容指纹和默认候选配置；8 道已参与 Prompt 决策的题只保留为诊断集，不进入最终总分或泛化结论。
- 冻结后新增 10 道 `stage16-holdout-v1` 留出题已完成逐题人工复核，标记为 `reviewed`，与冻结集和调优集做题面去重；它只允许作为一次性泛化验证输入，不进入冻结总分或调优。
- 限界前端已补齐文档上传、文档列表、解析、向量化、关键词索引和处理状态查看；它复用现有文档生命周期 API，不绕过元数据和失败补偿。
- Stage 17 增加显式 `/{document_id}/ocr` 后处理：只针对解析器标出的扫描版 PDF 图片页，使用可选 PaddleOCR 适配器和 pypdfium2 页面渲染；原生文字链路不自动调用 OCR，低置信度页不生成可检索切片。
- OCR 切片保存 `extraction_method=ocr` 以及供应商、模型版本、页码、行置信度和框坐标；文档暴露 `ocr_status`，便于重试、人工复核和失败归因。
- 工具路由支持百炼 Function Calling 演示配置；页面从 `/api/v1/info` 显示当前路由模式，模型只提出能力和用户原文中的报销单号，服务端继续负责身份、权限和 SQL。
- 新增标准库实现的助手小规模延迟/并发基准，记录请求数、成功数、P50/P95、路由和 `request_id`，只用于本地趋势比较，不作为生产 SLA。
- Stage 16 已按冻结 V1 + hybrid+RRF 配置完成百炼北京地域正式联调：20 道制度回答题与 12 道工具/合规题共 31 次模型调用，输入 28,635 token、输出 3,443 token，公开原价估算约 0.084814 元；3 道回答题保留为人工复核失败案例。完整报告位于 `data/evaluation/results/stage16_final_freeze.json`。
- Stage 12/13 路由与工具评测数据已版本化；当前只验证本地规则、图编排和安全边界，不把小样本路由题宣传成模型回答准确率。
- 固定 20 题的 V0/V1 回答对照 runner，记录状态、引用边界、越权来源安全、延迟和 token；人工回答要点复核单独保存在 `data/evaluation/results/stage11_answer_point_review.json`。
- 不复用原题措辞的 8 题 Stage 11 已核验调优集，覆盖多来源合并、无关例外抑制、条件式回答和相对期限；V2 当前只允许离线评测，不能作为线上默认版本。
- RAGAS 0.4.3 辅助入口复用冻结回答和召回上下文，评判忠实度、事实正确性和上下文召回；兼容依赖被显式锁定，且它不替代权限、日期、引用和人工要点硬指标。
- Ruff、Stage 12/13 回归测试已具备；完整测试数量以当前工作区运行结果为准，真实 Alembic upgrade/check、测试向量与真实 BGE 数据库闭环仍保留。

Stage 8、9、10 已分别由 `5e987d2`、`9863285`、`a590b99` 提交。Stage 10 正式基线在同一 40 题上得到：dense/hybrid 整题通过率均为 `1.0`，keyword 为 `0.975`；文档版本级 MRR 分别为 `0.9167/0.9211/0.9298`。完整报告位于 `data/evaluation/results/stage10_retrieval_comparison.json`。这些结果只衡量当前小型离线集上的文档版本检索，不代表回答准确率或生产效果。

Stage 15 正式本地 BGE 四组对照在同一 40 题 reviewed 集上完成：四组整题通过率、Recall@5、版本正确率和权限/有效期硬过滤准确率均为 `1.0`，硬过滤泄漏均为 `0`。`hybrid` 的 NDCG@5 为 `0.896579`、P95 为 `26.119ms`；加入确定性重写后 NDCG@5 降至 `0.889359`，加入 reranker 不提升 NDCG 且 P95 增至 `29.470ms`。因此线上默认回退 hybrid+RRF；完整报告位于 `data/evaluation/results/stage15_retrieval_ablation.json`。本次只评估本地检索，不代表百炼回答质量，也未产生额外 API 费用。

Stage 11 正式回答基线使用 `qwen3.7-plus-2026-05-26`、温度 0、关闭思考和同一 hybrid top-5。V0/V1 的硬通过率分别为 `0.85/0.80`，禁止来源安全率均为 `1.0`，且没有请求或结构化输出错误。20 题人工复核的完整通过率为 `0.20/0.30`，加权人工分为 `0.575/0.60`；这组原始结果单独看不足以决定默认 Prompt，最终结合独立调优集和安全边界选择 V1。完整机器报告位于 `data/evaluation/results/stage11_prompt_comparison.json`，人工复核位于 `data/evaluation/results/stage11_answer_point_review.json`。

独立 8 题调优集上，V0/V1/V2 自动硬通过率为 `0.625/0.75/0.75`，人工加权分为 `0.5625/0.625/0.5625`。V2 比 V1 多约 `12.3%` 输入 token，却没有稳定解决无关例外污染和多来源漏合并，因此不进入线上配置。V1 的 RAGAS 辅助结果为：忠实度 `0.9464`（14 个有效样本、1 个网络缺分）、事实正确性 `0.5013`、上下文召回 `0.9778`；人工与 RAGAS 存在明确冲突，所以不把该分数宣传成业务准确率。

## 项目资料

候选版演示、配置边界和已验证事实见 [候选版事实表](docs/candidate-version.md)；接口、启动方式和评测入口见本文后续章节。学习笔记保留在本地，不随公开代码发布。

## 本地启动

要求：Python 3.11、Docker Desktop，以及项目根目录已有 `.venv`。首次使用时将 `.env.example` 复制为 `.env`，不要提交真实 `.env`。

完整 Compose 演示（PostgreSQL、API、前端）：

```powershell
cd D:\xuexi\projects\agent_project
docker compose up -d
```

本地 embedding 开发（只启动数据库，API 使用宿主机虚拟环境）：

```powershell
cd D:\xuexi\projects\agent_project
docker compose up -d postgres
cd backend
..\.venv\Scripts\python.exe -m pip install -e ".[embedding,embedding-download,dev]"
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

需要处理扫描版 PDF 时，再显式安装 OCR 试点依赖；普通解析、检索和问答不依赖这些大包：

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

OCR 适配器显式使用 `PP-OCRv5`。Windows CPU 环境为规避 PaddlePaddle 3.3.1 的 oneDNN 兼容异常，会使用稳定的普通 Paddle 推理路径；第一次识别还需下载并加载检测、识别模型，耗时明显高于同进程后续请求。项目已完成一条合成图片型 PDF 的真实运行冒烟，但没有真实制度集识别率，因此不宣传 OCR 准确率。

若使用 Compose 演示 OCR，在 `.env` 中设置 `INSTALL_OCR=true`，然后重建 API 镜像；第一次构建/第一次识别会下载较大的运行时和模型：

```powershell
cd D:\xuexi\projects\agent_project
docker compose build --no-cache api
docker compose up -d
```

Compose API 镜像默认不打包体积较大的本地 BGE 依赖；需要真实 dense/hybrid 检索时使用上面的本地 embedding 开发路径，或在部署环境制作带 `[embedding]` 的专用镜像。

启动后可访问：

- OpenAPI：`http://127.0.0.1:8000/docs`
- 存活检查：`GET http://127.0.0.1:8000/api/v1/health`
- 就绪检查：`GET http://127.0.0.1:8000/api/v1/ready`
- 限界前端：`http://127.0.0.1:5173`
- 聚合观测：`GET http://127.0.0.1:8000/api/v1/observability/metrics`
- 请求轨迹：`GET http://127.0.0.1:8000/api/v1/observability/requests/{request_id}`

启用百炼 Function Calling 演示时，在本地 `.env` 中确认以下配置，并重启 API 或 Compose；`.env.example` 仍保持规则默认，便于无密钥环境运行：

```dotenv
DASHSCOPE_API_KEY=你的百炼密钥
TOOL_ROUTING_PROVIDER=bailian
TOOL_ROUTING_MODEL_NAME=qwen-plus
```

前端右上角显示“百炼 Function Calling”后，再演示本人报销状态或合规预检查。政策问题仍走制度 RAG，模型不会获得用户 ID、权限范围或数据库参数。

文档上传入口位于问答区域下方：先填知识库 ID，点击“刷新文档”查看现有文档；新文件上传后，点击对应行的“解析并建索引”，页面会按“解析 -> embedding -> 关键词索引”顺序执行并显示状态。

小规模延迟基准需由演示者主动运行（会按当前配置调用真实 API，因此可能产生模型费用）：

```powershell
cd D:\xuexi\projects\agent_project\backend
$env:BENCHMARK_JWT="可选的演示 JWT"
..\.venv\Scripts\python.exe -m app.evaluation.assistant_benchmark `
  --knowledge-base-id "你的知识库 UUID" `
  --concurrency 1 2 `
  --repeat 1
```

留出题文件为 `data/evaluation/stage16_holdout_v1.json`，已逐题人工复核并标记为 `reviewed`。它只允许作为一次性泛化验证输入，不会自动进入 Stage 16 冻结总分，也不能用于反向调参。

## 当前文档 API

```text
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PATCH  /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/metadata
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/process
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ocr
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/embeddings
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/keyword-index
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks
DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/keyword
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/hybrid
POST   /api/v1/knowledge-bases/{knowledge_base_id}/answer
POST   /api/v1/knowledge-bases/{knowledge_base_id}/assistant
```

重复内容按“同一知识库内 SHA-256 相同”判断，返回 `409` 和已有文档 ID。Markdown/TXT 必须使用 UTF-8。PDF 必须包含有效文件头和结束标记；DOCX 必须是包含必要 Word 条目的未加密 ZIP，且受到解压总大小限制。

当前存储使用本地 `uploads/`。数据库只保存相对 `storage_key`，API 不向客户端暴露服务器路径。Stage 17 只试点扫描版 PDF 的逐页 OCR：必须显式点击 OCR，且依赖 `[ocr]` 额外安装；不接收独立 JPG/PNG，不做发票字段抽取、图片问答、视觉向量检索、自动验真、MinIO/S3 或生产级对象存储。

## 质量检查

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m alembic current
..\.venv\Scripts\python.exe -m alembic check
docker compose config
```

## 项目结构

```text
agent_project/
├─ backend/
│  ├─ app/
│  │  ├─ api/                 # HTTP 路由与请求编排
│  │  ├─ core/                # 配置
│  │  ├─ db/                  # SQLAlchemy 会话与元数据
│  │  ├─ evaluation/          # 版本化评测数据契约
│  │  ├─ models/              # 文档与可追溯分块 ORM 模型
│  │  ├─ schemas/             # API 数据契约
│  │  └─ services/            # 存储、校验、解析、切片和处理编排
│  ├─ alembic/                # 数据库迁移
│  └─ tests/                  # 自动化测试和结构化样本生成器
├─ data/                      # 垂直制度样本、manifest 和评测集
├─ docs/                      # 环境、学习与项目交接文档
├─ infra/postgres/init/       # pgvector 与教学表初始化
├─ uploads/                   # 本地运行数据，不进入 Git
└─ compose.yaml
```

## 项目原则

- 所有数据库结构变化必须有 Alembic 迁移。
- 业务代码与模型、存储供应商解耦。
- 文件名、MIME、模型输出和上传文档内容都不能被默认信任。
- 每个阶段必须能运行、能测试、能解释失败与补偿路径。
- 简历中的每项能力最终都要有代码、测试、评测或演示证据。

完整执行路线见 [修订版交接文档](docs/企业差旅报销RAG项目交接文档_修订版_2026-07-26.md)。
