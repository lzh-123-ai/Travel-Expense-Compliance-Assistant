# Enterprise Travel Reimbursement RAG Assistant

企业国内差旅报销制度合规助手。系统目标是根据费用发生日期、制度版本和用户权限，返回能够追溯到原文的办理依据；没有足够证据时拒绝下结论。

这是一个兼顾学习与秋招作品集的项目。当前先把文档摄取做可靠，再逐步完成结构化解析、版本感知检索、引用问答、权限过滤和离线评测，不用技术名词代替可验证结果。

## 当前进度

当前处于 **Stage 11：引用回答与拒答收尾**。回答核心、V0/V1 Prompt、引用白名单、百炼 LangChain Provider、公共回答 API、正式对照 runner、20 题回答要点人工复核、8 题 V0/V1/V2 调优对照和 V1 RAGAS 辅助校准均已完成。调优结果不支持晋级 V2，线上默认继续使用 V1；V2 保留为可复现的失败实验。

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
- 固定 20 题的 V0/V1 回答对照 runner，记录状态、引用边界、越权来源安全、延迟和 token；人工回答要点复核单独保存在 `data/evaluation/results/stage11_answer_point_review.json`。
- 不复用原题措辞的 8 题 Stage 11 已核验调优集，覆盖多来源合并、无关例外抑制、条件式回答和相对期限；V2 当前只允许离线评测，不能作为线上默认版本。
- RAGAS 0.4.3 辅助入口复用冻结回答和召回上下文，评判忠实度、事实正确性和上下文召回；兼容依赖被显式锁定，且它不替代权限、日期、引用和人工要点硬指标。
- Ruff、124 项自动化测试、真实 Alembic upgrade/check、测试向量与真实 BGE 数据库闭环通过。

Stage 8、9、10 已分别由 `5e987d2`、`9863285`、`a590b99` 提交。Stage 10 正式基线在同一 40 题上得到：dense/hybrid 整题通过率均为 `1.0`，keyword 为 `0.975`；文档版本级 MRR 分别为 `0.9167/0.9211/0.9298`。完整报告位于 `data/evaluation/results/stage10_retrieval_comparison.json`。这些结果只衡量当前小型离线集上的文档版本检索，不代表回答准确率或生产效果。

Stage 11 正式回答基线使用 `qwen3.7-plus-2026-05-26`、温度 0、关闭思考和同一 hybrid top-5。V0/V1 的硬通过率分别为 `0.85/0.80`，禁止来源安全率均为 `1.0`，且没有请求或结构化输出错误。20 题人工复核的完整通过率为 `0.20/0.30`，加权人工分为 `0.575/0.60`；这组原始结果单独看不足以决定默认 Prompt，最终结合独立调优集和安全边界选择 V1。完整机器报告位于 `data/evaluation/results/stage11_prompt_comparison.json`，人工复核位于 `data/evaluation/results/stage11_answer_point_review.json`。

独立 8 题调优集上，V0/V1/V2 自动硬通过率为 `0.625/0.75/0.75`，人工加权分为 `0.5625/0.625/0.5625`。V2 比 V1 多约 `12.3%` 输入 token，却没有稳定解决无关例外污染和多来源漏合并，因此不进入线上配置。V1 的 RAGAS 辅助结果为：忠实度 `0.9464`（14 个有效样本、1 个网络缺分）、事实正确性 `0.5013`、上下文召回 `0.9778`；人工与 RAGAS 存在明确冲突，所以不把该分数宣传成业务准确率。

## 学习入口

当前先看 [代码导航地图](docs/learning/code-navigation-map.md) 与 [代码注释与定位约定](docs/learning/code-commenting-guide.md)，再从 [Stage 11 学习说明](docs/learning/stage-11-answering-and-evaluation.md)完成本阶段验收；随后按约定进行 Stage 7–11 轻量回溯训练，再进入 Stage 12 Function Calling。[Stage 10 学习说明](docs/learning/stage-10-keyword-and-hybrid-retrieval.md)继续作为 hybrid 检索复盘入口。

## 本地启动

要求：Python 3.11、Docker Desktop，以及项目根目录已有 `.venv`。首次使用时将 `.env.example` 复制为 `.env`，不要提交真实 `.env`。

```powershell
cd D:\xuexi\projects\agent_project
docker compose up -d
cd backend
..\.venv\Scripts\python.exe -m pip install -e ".[embedding,embedding-download,dev]"
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

启动后可访问：

- OpenAPI：`http://127.0.0.1:8000/docs`
- 存活检查：`GET http://127.0.0.1:8000/api/v1/health`
- 就绪检查：`GET http://127.0.0.1:8000/api/v1/ready`

## 当前文档 API

```text
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
PATCH  /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/metadata
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/process
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/embeddings
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/keyword-index
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks
DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/keyword
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/hybrid
POST   /api/v1/knowledge-bases/{knowledge_base_id}/answer
```

重复内容按“同一知识库内 SHA-256 相同”判断，返回 `409` 和已有文档 ID。Markdown/TXT 必须使用 UTF-8。PDF 必须包含有效文件头和结束标记；DOCX 必须是包含必要 Word 条目的未加密 ZIP，且受到解压总大小限制。

当前存储使用本地 `uploads/`。数据库只保存相对 `storage_key`，API 不向客户端暴露服务器路径。系统会检测并报告疑似扫描页或未索引图片，但尚未声称支持 OCR、图片理解、MinIO/S3 或生产级对象存储。

## 质量检查

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m alembic current
..\.venv\Scripts\python.exe -m alembic check
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
