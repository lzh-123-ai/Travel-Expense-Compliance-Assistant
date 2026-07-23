# Enterprise RAG Assistant

企业内部规章制度与业务 SOP 智能知识助手。

这是一个以学习和求职作品为双重目标的项目。我们会逐步实现文档管理、向量检索、可溯源问答、权限控制、RAG 评测和 Agent 工作流，而不是一次性堆出无法解释的代码。

## 当前进度

当前处于 **Stage 3：FastAPI 异步数据库连接**。

已经具备：

- FastAPI 分层项目骨架
- 基于环境变量的配置
- `/health` 健康检查接口
- 第一组自动化测试
- Windows 环境安装指南
- 面向初学者的代码阅读指南

Stage 1 已完成，并通过 `ruff check .` 与 3 项自动化测试。

当前正在完成：

- 使用 SQLAlchemy AsyncEngine 管理连接池
- 为每个请求提供独立 AsyncSession
- 区分 API 存活检查与数据库就绪检查

暂未加入 ORM、模型调用、RAG 和前端。这些会在理解并验收当前阶段后逐步加入。

## 项目结构

```text
agent_project/
├─ backend/                 # Python 后端
│  ├─ app/
│  │  ├─ api/              # HTTP 路由层
│  │  ├─ core/             # 配置等全局基础能力
│  │  ├─ schemas/          # 请求和响应的数据结构
│  │  └─ main.py           # FastAPI 应用入口
│  ├─ tests/               # 后端自动化测试
│  └─ pyproject.toml        # Python 依赖和工具配置
├─ docs/
│  ├─ setup/               # 环境安装与运行文档
│  └─ learning/            # 按阶段编写的学习文档
├─ infra/postgres/init/     # PostgreSQL 首次启动脚本
├─ compose.yaml             # 本地基础设施服务
├─ .env.example            # 可公开的环境变量模板
└─ README.md
```

## 开始学习

1. 回顾已完成的 [Stage 1 学习指南](docs/learning/stage-01-foundation.md)。
2. 回顾已完成的 [Stage 2：Docker 与 PostgreSQL](docs/learning/stage-02-docker-postgres.md)。
3. 阅读 [Stage 3：FastAPI 异步连接 PostgreSQL](docs/learning/stage-03-async-database.md)。
4. 完成 Stage 3 文档末尾的失败路径测试。

## 项目原则

- 业务代码与具体模型供应商解耦。
- 每个阶段都必须能运行、能测试、能解释。
- API Key 和密码只通过环境变量传入。
- 关键代码解释“为什么”，不使用注释重复 Python 基础语法。
- 简历中的每项能力最终都要有代码、测试或演示作为证据。
