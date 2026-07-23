# Stage 4：Alembic 迁移与知识库模型

本阶段把“数据库能连接”推进为“数据库结构可被版本管理”。完成后，项目拥有第一个正式业务实体：KnowledgeBase（知识库）。

## 本阶段目标

1. 使用 Alembic 记录数据库模式的每一次变化。
2. 创建 knowledge_bases 表，而不是在应用启动时调用 create_all() 建表。
3. 学会审阅自动生成的迁移，尤其是识别不应执行的删除操作。
4. 能查看数据库当前迁移版本，并安全地升级到最新版本。

## 为什么需要迁移

ORM 模型只是 Python 中对“期望结构”的描述，已经运行的 PostgreSQL 不会因为模型改动而自动变更。直接自动建表在多人协作、部署或回滚时容易失控：无法清楚知道每个环境变更过什么。

Alembic 迁移文件像 Git 提交一样保存数据库结构的演进：

    模型定义（期望结构）
            ↓
    Alembic revision（可审阅的结构变更）
            ↓
    alembic upgrade head（应用变更）
            ↓
    PostgreSQL + alembic_version（真实结构与当前版本）

## 本阶段新增内容

| 文件 | 用途 |
| --- | --- |
| backend/app/db/base.py | 所有 SQLAlchemy ORM 模型的共同基类 Base。 |
| backend/app/models/knowledge_base.py | 第一个业务模型 KnowledgeBase。 |
| backend/app/models/__init__.py | 统一导入模型，确保 Alembic 能看到所有表。 |
| backend/alembic.ini | Alembic 的基础配置。 |
| backend/alembic/env.py | 异步数据库迁移入口。 |
| backend/alembic/versions/40944bd0aafd_create_knowledge_bases.py | 创建知识库表的第一份迁移。 |
| backend/tests/test_knowledge_base_model.py | 模型结构的快速单元测试。 |

## KnowledgeBase 模型

当前表结构刻意保持简单：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键；由应用在插入前生成。 |
| name | VARCHAR(120) | 知识库名称；不能为空且唯一。 |
| description | TEXT | 可选描述。 |
| created_at | 带时区的时间 | 由数据库在创建时写入。 |
| updated_at | 带时区的时间 | ORM 更新对象时会刷新。 |

后续的 Document、文本切片和权限规则都将通过外键关联到 knowledge_bases.id，因此它是整个 RAG 数据模型的起点。

## 异步 Alembic 的关键点

应用使用 asyncpg，数据库 URL 是 postgresql+asyncpg 协议。Alembic 的核心迁移 API 仍是同步的，因此 env.py 使用以下桥接：

    AsyncEngine.connect()
            ↓
    AsyncConnection.run_sync(do_run_migrations)
            ↓
    Alembic 在同步 Connection 上执行 DDL

迁移专门使用 NullPool，避免命令行短任务留下连接池；Web 应用正常运行时仍使用 app/db/session.py 中的连接池。

## 自动生成不等于自动执行

本次自动生成发现了 knowledge_bases，也发现了 learning_check。后者是 Stage 2 手动创建、用于验证 Docker 卷持久化的练习表，没有对应 ORM 模型。若直接接受生成结果，Alembic 会把它误判为“应删除的表”。

因此迁移中明确移除了删除或重新创建 learning_check 的语句。这个例子说明：自动生成只是草稿，每一份迁移都必须人工审阅，特别是 drop_table、drop_column 和数据迁移。

## 常用命令

在项目根目录激活虚拟环境后：

    Set-Location backend

    # 查看本地代码所拥有的最新迁移
    ..\.venv\Scripts\python.exe -m alembic heads

    # 查看数据库已应用到哪个版本
    ..\.venv\Scripts\python.exe -m alembic current

    # 升级到最新版本
    ..\.venv\Scripts\python.exe -m alembic upgrade head

    # 仅在确认可以删除该迁移创建的表时，回退一个版本
    ..\.venv\Scripts\python.exe -m alembic downgrade -1

downgrade -1 会删除 knowledge_bases 及其中数据；在有真实数据的环境中，必须先备份并确认影响范围。不要把它当作普通的“撤销”按钮。

## 本阶段验收

    Set-Location backend
    ..\.venv\Scripts\python.exe -m ruff format .
    ..\.venv\Scripts\python.exe -m ruff check .
    ..\.venv\Scripts\python.exe -m pytest
    ..\.venv\Scripts\python.exe -m alembic current

预期结果：Ruff 无错误；测试通过；Alembic 当前版本为 40944bd0aafd (head)；PostgreSQL 中同时存在 alembic_version、knowledge_bases 和 Stage 2 的 learning_check。

## 自检问题

1. 为什么 ORM 模型发生变化后，不能只重启 FastAPI 就期望数据库结构自动变化？
2. Base.metadata 在 Alembic 自动生成中扮演什么角色？
3. 自动生成迁移时为什么必须特别审查删除表、删除列的操作？
4. Web 应用使用连接池，而 Alembic 使用 NullPool，两者分别适合什么场景？
5. 为什么不能在生产数据库上随意执行 alembic downgrade -1？
