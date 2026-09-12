# Enterprise Travel Expense Compliance Assistant

面向企业差旅报销场景的证据型 RAG 助手。系统把制度版本、费用日期、员工访问范围和报销事实放进同一条可追溯链路，支持制度问答、本人报销状态查询和合规预检查。

项目重点不是让模型自由回答，而是让结论先经过服务端边界校验：知识库、日期、状态和权限先过滤，模型只处理允许进入上下文的证据；证据不足时明确追问或拒答。

## 解决的问题

- 制度分散在不同版本的 PDF、DOCX、Markdown 和 TXT 中，员工难以找到适用条款。
- 同一城市或费用类型在不同日期、不同通知下标准不同，不能只按语义相似度回答。
- 个人报销状态属于业务数据，必须按服务端身份和员工范围查询，不能交给模型猜测。
- 生成式回答需要同时满足来源可追溯、权限不越界和失败可定位。

## 核心能力

- 文件摄取：扩展名、MIME 和实际结构联合校验，流式 SHA-256 去重，原子存储和失败补偿。
- 内容处理：PDF、DOCX、Markdown、UTF-8 TXT 解析；保留页码或标题路径、表格和图片页边界。
- 版本感知：按知识库、费用日期、生效区间、访问范围和替代关系过滤制度。
- 混合检索：本地 BGE dense、PostgreSQL GIN 关键词检索和文档感知 RRF 融合。
- 可控回答：固定回答状态、证据白名单、引用来源、确定性追问和无证据拒答。
- 业务工具：制度问答、本人报销状态和合规预检查；工具参数由服务端校验，SQL 始终绑定服务端身份。
- 可观测性：统一 `request_id`，记录路由、工具、检索、模型和合规节点的耗时、失败类型及用量。
- 受控 OCR：扫描版 PDF 通过显式接口按页处理，低置信度结果不写入可检索知识库；普通解析链路不加载 OCR。

## 演示流程

启动后访问 `http://localhost:5173`，可按下面顺序体验核心链路：

1. 输入带费用日期的制度问题，查看适用制度版本及其页码或章节路径。
2. 查询“我的报销单到哪一步了”，确认报销事实只按服务端身份和员工范围读取。
3. 对本人报销单发起合规预检查，页面分别展示事实、制度依据、建议和 `request_id`。
4. 通过 `GET /api/v1/observability/requests/{request_id}` 查看路由、检索、模型和工具节点的耗时与失败状态。

演示数据和制度均为虚构内容；系统不自动批准、驳回或修改真实报销单。

## 架构概览

```text
文档上传
  -> 结构校验、哈希去重、原子存储
  -> 解析与确定性切片
  -> embedding / 关键词索引
  -> 日期、知识库、权限硬过滤
  -> dense + keyword + RRF
  -> 证据白名单回答

用户问题
  -> 服务端身份解析
  -> 制度问答 / 报销状态 / 合规预检查
  -> 只读工具或检索服务
  -> 结构化结果与可追踪引用
```

模型只负责回答生成或提出受限的工具意图；身份、权限、费用日期、SQL 条件、工具结果和引用校验均在服务端完成。

## 验证结果

公开评测使用 6 份虚构制度、30 个切片、20 道制度回答题和 12 道工具/合规题。结果用于验证工程链路和回归，不代表生产准确率、容量或 SLA。

- 40 道人工复核检索题中，默认 `hybrid + RRF` 的 Recall@5、版本正确率和权限/有效期过滤准确率均为 `1.0`，硬过滤泄漏为 `0`。
- 本地 BGE 混合检索 NDCG@5 为 `0.896579`，P95 延迟为 `26.119ms`；查询重写和重排保留为实验能力，当前没有稳定净收益。
- 百炼冻结回答集共 20 题，状态与引用联合检查通过 `17/20`；引用安全率为 `1.0`。已人工复核的失败集中于多来源证据漏引、无关例外引用和条件明确时的过度追问。
- 工具与合规联调共 12 题，全部通过，未出现未授权参数错误。
- RAGAS 仅作为辅助视角，不替代权限、日期、引用白名单和人工抽样等领域指标。

## 快速启动

环境要求：Python 3.11、Docker Desktop，以及项目根目录的 `.venv`。

```powershell
cd D:\xuexi\projects\agent_project
Copy-Item .env.example .env
docker compose up -d --build
```

服务地址：

- API 文档：`http://localhost:8000/docs`
- 存活检查：`http://localhost:8000/api/v1/health`
- 就绪检查：`http://localhost:8000/api/v1/ready`
- Web 界面：`http://localhost:5173`

启用百炼回答或工具路由时，在本地 `.env` 配置密钥，不要提交真实 `.env`：

```dotenv
DASHSCOPE_API_KEY=你的百炼密钥
TOOL_ROUTING_PROVIDER=bailian
TOOL_ROUTING_MODEL_NAME=qwen-plus
```

## 主要 API

```text
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET    /api/v1/knowledge-bases/{knowledge_base_id}/documents
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/process
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/ocr
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/embeddings
POST   /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/keyword-index
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/keyword
POST   /api/v1/knowledge-bases/{knowledge_base_id}/search/hybrid
POST   /api/v1/knowledge-bases/{knowledge_base_id}/answer
POST   /api/v1/knowledge-bases/{knowledge_base_id}/assistant
GET    /api/v1/observability/requests/{request_id}
```

文档重复按“同一知识库内 SHA-256 相同”判断，并由数据库唯一约束兜底并发上传。数据库只保存相对 `storage_key`，不向客户端暴露服务器路径。

## 质量检查

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m ruff check app tests
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m alembic check
docker compose config
```

## 项目结构

```text
agent_project/
├─ backend/app/api/          # HTTP 路由与请求编排
├─ backend/app/services/     # 存储、校验、解析、检索、回答和业务工具
├─ backend/app/models/       # 文档、切片、报销记录和审计模型
├─ backend/app/evaluation/   # 评测契约、指标和运行器
├─ backend/alembic/          # 数据库迁移
├─ backend/tests/             # 自动化测试和边界样本
├─ frontend/                 # 限界演示界面
├─ data/policies/             # 虚构制度样本
├─ data/evaluation/           # 版本化评测集和结果
├─ docs/candidate-version.md # 候选版事实、配置和边界
├─ infra/postgres/            # pgvector 初始化脚本
└─ compose.yaml
```

## 边界与安全

- 示例制度和报销数据均为虚构数据，不能直接作为真实企业制度。
- 不做发票字段抽取、图片问答、视觉向量检索、自动验真或生产级对象存储。
- OCR 必须显式触发并安装可选依赖；普通文本解析不会自动调用 OCR。
- 客户端身份、权限、SQL 查询和模型输出均不作为可信边界；服务端负责最终校验。
- 真实密钥只从环境变量读取，不进入源码、测试数据、日志或 Git 历史。

## 开源说明

项目用于展示企业 RAG 应用的后端工程、检索评测和安全边界设计。欢迎通过 Issue 反馈问题或提出改进建议。
