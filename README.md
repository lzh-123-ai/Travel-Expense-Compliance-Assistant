# Enterprise Travel Reimbursement RAG Assistant

企业国内差旅报销制度合规助手。系统目标是根据费用发生日期、制度版本和用户权限，返回能够追溯到原文的办理依据；没有足够证据时拒绝下结论。

这是一个兼顾学习与秋招作品集的项目。当前先把文档摄取做可靠，再逐步完成结构化解析、版本感知检索、引用问答、权限过滤和离线评测，不用技术名词代替可验证结果。

## 当前进度

当前处于 **Stage 7：生产化文档摄取**。

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
- Ruff 通过，45 项自动化测试通过。

下一阶段是 Stage 8：解析器注册表、PDF/DOCX 结构化解析、制度版本元数据和可追溯切片。embedding、模型问答和前端尚未实现。

## 学习入口

第一次阅读 Stage 7 不要从目录逐个文件硬看。请从[Stage 7 学习说明的推荐阅读顺序](docs/learning/stage-07-document-ingestion.md#从哪里开始推荐阅读顺序)开始：先运行一条成功上传测试，再按“测试 → 路由 → 校验服务 → 存储服务 → 模型/迁移”的顺序追踪主链路，之后才看失败清理和删除补偿。

## 本地启动

要求：Python 3.11、Docker Desktop，以及项目根目录已有 `.venv`。首次使用时将 `.env.example` 复制为 `.env`，不要提交真实 `.env`。

```powershell
cd D:\xuexi\projects\agent_project
docker compose up -d
cd backend
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
DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}
```

重复内容按“同一知识库内 SHA-256 相同”判断，返回 `409` 和已有文档 ID。Markdown/TXT 必须使用 UTF-8。PDF 必须包含有效文件头和结束标记；DOCX 必须是包含必要 Word 条目的未加密 ZIP，且受到解压总大小限制。

当前存储使用本地 `uploads/`。数据库只保存相对 `storage_key`，API 不向客户端暴露服务器路径。项目尚未声称支持 OCR、MinIO/S3 或生产级对象存储。

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
│  │  ├─ models/              # ORM 模型
│  │  ├─ schemas/             # API 数据契约
│  │  └─ services/            # 存储、校验等可替换业务服务
│  ├─ alembic/                # 数据库迁移
│  └─ tests/                  # 自动化测试和结构化样本生成器
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
