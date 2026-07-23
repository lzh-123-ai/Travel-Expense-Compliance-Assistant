# Stage 3：FastAPI 异步连接 PostgreSQL

## 本节目标

完成本节后，你应该能够解释：

1. Engine、连接池、Connection 和 Session 的职责差异。
2. 为什么每个请求应该获得独立的 `AsyncSession`。
3. FastAPI 的 `Depends` 如何提供并自动关闭 Session。
4. `/health` 与 `/ready` 为什么不能混为同一个接口。
5. 测试为什么要替换数据库依赖，而不是每次都连接 Docker。

## 1. 连接 URL

项目通过以下形式连接数据库：

```text
postgresql+asyncpg://用户名:密码@主机:端口/数据库名
```

- `postgresql` 是数据库类型。
- `asyncpg` 是 SQLAlchemy 使用的异步 PostgreSQL 驱动。
- FastAPI 在 Windows 主机运行，因此主机使用 `localhost`。
- 以后 FastAPI 也进入 Compose 后，主机名会改为服务名 `postgres`。

真实连接字符串保存在 `.env` 的 `DATABASE_URL` 中。源码中的值只是本地开发默认值，确保测试导入应用时有完整配置。

## 2. Engine 与 Session

[session.py](../../backend/app/db/session.py) 创建了两个长期对象：

```text
AsyncEngine
  -> 管理数据库驱动和连接池

async_session_factory
  -> 按需创建 AsyncSession
```

Engine 通常随应用存在，不应该每个请求都重新创建。Session 表示一次工作单元和事务上下文，不应该被多个并发请求共享。

官方 SQLAlchemy 文档特别指出，一个 `AsyncSession` 不能在多个并发任务之间共享。因此 `get_db_session()` 每次被 FastAPI 调用时都会创建一个新 Session，并在 `async with` 结束时关闭它。

## 3. FastAPI 依赖注入

[ready.py](../../backend/app/api/routes/ready.py) 中：

```python
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
```

它表示：路由需要一个 `AsyncSession`，但路由不负责手动创建它。FastAPI 调用 `get_db_session()`，把产生的 Session 传给路由，请求结束后继续执行生成器的清理过程。

这样路由只关心查询，资源创建与释放集中在数据库模块。

## 4. 为什么同时需要 health 和 ready

```text
GET /api/v1/health
  -> API 进程是否活着
  -> 不访问数据库

GET /api/v1/ready
  -> API 是否具备处理真实业务的条件
  -> 执行 SELECT 1 检查数据库
```

如果数据库暂时故障，API 进程仍然活着，不应该被无限重启；但它还没有准备好接收真实流量，所以 `/ready` 返回 503。

## 5. 为什么测试替换数据库依赖

单元测试应该快速、稳定，并且不依赖开发者是否启动 Docker。[test_ready.py](../../backend/tests/test_ready.py) 使用 `app.dependency_overrides` 把真实 `get_db_session()` 替换成模拟 Session。

当前成功测试验证：

- 路由调用数据库 Session。
- 数据库没有抛出异常时返回 200。
- JSON 符合响应模型。

真实数据库连接会通过单独的集成检查验证。以后测试套件会区分单元测试和集成测试。

## 6. 安装更新后的依赖

在项目根目录并已激活 `.venv` 的 PowerShell 中执行：

```powershell
python -m pip install -e ".\backend[dev]"
```

然后进入后端目录，使用项目提供的脚本验证真实连接：

```powershell
cd backend
python scripts/check_database.py
```

成功结果会同时显示数据库名称和 vector 扩展版本。然后运行 API：

```powershell
python -m uvicorn app.main:app --reload
```

访问：

- <http://127.0.0.1:8000/api/v1/health>
- <http://127.0.0.1:8000/api/v1/ready>

## 7. 你的代码任务：补充数据库失败测试

成功路径已经在 `test_ready.py` 中作为示例。请增加一个测试，验证数据库执行 SQL 时抛出 `SQLAlchemyError`，接口会返回：

```json
{
  "detail": "Database is unavailable"
}
```

要求：

1. 新建一个失败版依赖覆盖函数。
2. 使用 `AsyncMock(spec=AsyncSession)` 创建模拟 Session。
3. 让 `session.execute` 在等待时抛出 `SQLAlchemyError`。
4. 在 `try/finally` 中设置并清理 `app.dependency_overrides`。
5. 断言状态码为 503，并断言完整 JSON。
6. 不连接真实 Docker 数据库。

提示：`AsyncMock` 可以这样配置异步异常：

```python
session.execute.side_effect = SQLAlchemyError("database unavailable")
```

完成后应有 5 项测试：

```powershell
python -m ruff format .
python -m ruff check .
pytest
```

## 自检问题

1. 为什么 Engine 可以全局创建，而 Session 不应该全局共享？
2. `pool_pre_ping=True` 解决什么问题？
3. `yield session` 后面的清理逻辑什么时候执行？
4. 数据库断开时，为什么 `/health` 仍可返回 200，而 `/ready` 应返回 503？
5. 为什么单元测试不直接依赖本机 Docker PostgreSQL？
