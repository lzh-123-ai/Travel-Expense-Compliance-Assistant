# Stage 1：从脚本到后端工程

## 本节目标

完成本节后，你应该能够解释：

1. 为什么项目不把所有代码写在 `main.py`。
2. 请求如何从 URL 到达路由函数。
3. `response_model` 为什么不仅是类型提示。
4. 为什么配置放在环境变量中。
5. 自动化测试验证了什么。

暂时不要求你学习数据库、Dockerfile 或 LangChain。

## 1. 一次请求经过了什么

访问：

```text
GET /api/v1/health
```

代码链路是：

```text
Uvicorn 启动 app.main:app
  -> create_app() 创建 FastAPI 对象
  -> include_router() 注册统一 API 路由
  -> health.router 匹配 /health
  -> health_check() 读取配置并构造 HealthResponse
  -> Pydantic 校验响应
  -> FastAPI 转成 JSON 返回
```

请依次打开这些文件阅读：

1. `backend/app/main.py`
2. `backend/app/api/router.py`
3. `backend/app/api/routes/health.py`
4. `backend/app/schemas/health.py`
5. `backend/app/core/config.py`

## 2. 为什么要使用应用工厂

`create_app()` 负责创建 FastAPI 实例。直接写 `app = FastAPI()` 在小练习中没有问题，但工厂函数更容易：

- 在测试中创建不同配置的应用。
- 按环境开启或关闭功能。
- 统一注册路由、中间件和异常处理器。

文件底部的 `app = create_app()` 是给 Uvicorn 使用的实际应用对象。

## 3. 路由为什么分层

`api/router.py` 是路由总入口。以后用户、知识库、文档、聊天都会有自己的路由文件，总入口只负责组装它们。

路由层以后只应该负责：

- 接收并校验 HTTP 输入。
- 调用业务服务。
- 返回约定的响应。

它不应该直接包含复杂检索、数据库事务或模型 Prompt。这样才能独立测试业务逻辑，也方便将来更换网页、命令行等入口。

## 4. `HealthResponse` 的价值

`response_model=HealthResponse` 会把它写入 OpenAPI 文档，并在返回时校验和序列化数据。它等于公开声明：

```json
{
  "status": "ok",
  "app_name": "Enterprise RAG Assistant",
  "environment": "development"
}
```

如果以后误返回了错误类型，测试或运行过程可以尽早暴露问题。API 的请求和响应结构就是前后端之间的合同。

## 5. 配置为什么不用硬编码

开发、测试和生产环境会使用不同数据库、密码和 API Key。把这些值写死在源码中会导致泄密，也会让部署困难。

`.env.example` 只说明有哪些配置，可以提交到 Git；真实 `.env` 保存本机值，已被 `.gitignore` 排除。

`@lru_cache` 让 `Settings` 在一个进程中只解析一次，避免每个请求重复读取环境。

## 6. 测试在验证什么

`tests/test_health.py` 没有真正打开浏览器或占用端口。`TestClient` 在进程内模拟 HTTP 请求，但仍然会经过 FastAPI 的路由和响应校验。

第一个测试验证正确接口及完整 JSON；第二个测试验证不存在的 URL 会返回 404。这是一组很小的回归保护：后续修改路由时，我们可以立刻知道基础接口有没有被破坏。

## 7. 你的第一个任务

在成功启动服务并通过测试后，请你自己完成一个 `GET /api/v1/info` 接口，要求返回：

```json
{
  "project": "Enterprise RAG Assistant",
  "stage": 1,
  "features": ["health-check"]
}
```

要求：

- 在 `schemas` 中定义响应模型。
- 新建或选择合理的路由文件。
- 注册路由。
- 增加一个测试，校验状态码和完整响应。
- 不照搬健康检查函数名。

这个任务不是考语法，而是确认你已经看懂“Schema -> Route -> Router -> Test”的连接方式。完成后把结果告诉我，我会先检查和讲解，再进入数据库与 Docker 小节。

## 自检问题

先尝试不用搜索回答：

1. 修改 `api_v1_prefix` 后，为什么路由函数本身不用改？
2. 删除 `application.include_router(...)` 会发生什么？
3. 为什么 `.env.example` 可以提交，而 `.env` 不应该提交？
4. `async def` 一定会让代码更快吗？为什么？

