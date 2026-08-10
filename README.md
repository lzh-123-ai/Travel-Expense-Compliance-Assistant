# Enterprise Travel Reimbursement RAG Assistant

企业国内差旅报销制度合规助手。系统目标是根据费用发生日期、制度版本和用户权限，返回能够追溯到原文的办理依据；没有足够证据时拒绝下结论。

这是一个兼顾学习与秋招作品集的项目。当前先把文档摄取做可靠，再逐步完成结构化解析、版本感知检索、引用问答、权限过滤和离线评测，不用技术名词代替可验证结果。

## 当前进度

**Stage 10：中文关键词与 hybrid 检索已完成工程实现、正式对照和学习验收，下一步进入 Stage 11 引用回答与拒答。**

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
- Ruff、107 项自动化测试、真实 Alembic upgrade/check、测试向量与真实 BGE 数据库闭环通过。

Stage 8 已由 `5e987d2` 提交，Stage 9 已由 `9863285` 提交。Stage 10 正式基线在同一 40 题上得到：dense/hybrid 整题通过率均为 `1.0`，keyword 为 `0.975`；文档版本级 MRR 分别为 `0.9167/0.9211/0.9298`。完整报告位于 `data/evaluation/results/stage10_retrieval_comparison.json`。这些结果只衡量当前小型离线集上的文档版本检索，不代表回答准确率或生产效果。

## 学习入口

当前从 [Stage 10 学习说明](docs/learning/stage-10-keyword-and-hybrid-retrieval.md)复盘 hybrid 检索；Stage 11 将从固定回答契约、引用和拒答边界切入。

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
