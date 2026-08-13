# 代码注释与定位约定

本项目不要求记住文件名或函数名。阅读和修改代码时，优先记住职责、搜索词和
调用方向：

```text
HTTP Route -> Service -> ORM/存储/Provider -> HTTP 响应或评测报告
```

## 如何定位代码

1. 从 `backend/app/main.py` 开始，再沿 `backend/app/api/router.py` 进入业务路由。
2. 按工作流选 Route：上传/处理看 `api/routes/documents.py`，检索看
   `api/routes/retrieval.py`，回答看 `api/routes/answers.py`。
3. 先读 Route 的 docstring，再沿其 Service 调用进入业务主链路。
4. 只有需要字段、约束、HTTP 数据结构或风险边界证明时，才打开 `models/`、
   `schemas/`、迁移或测试。

常用概念搜索词：`upload_document`、`process_document`、`index_document`、
`allowed_scopes`、`expense_date`、`hybrid_search`、`citation_ids`、
`IntegrityError`、`quarantine`。

## 注释最低标准

新写或发生实质改动的业务代码，必须让第一次阅读的人在一两分钟内回答：

1. 入口、调用方和下游依赖分别是什么？
2. 输入输出是什么，关键状态在哪里持久化？
3. 为什么需要过滤、锁、事务、补偿或白名单？失败会留下什么状态？

约定：

- 每个业务模块开头用 3-8 行说明职责、上游和下游；离线评测脚本要明确不是
  请求链路代码。
- 公共 Service、复杂 Route、协议和模型要用 docstring 说明输入、输出、边界和
  失败结果。
- 并发、权限、日期过滤、新鲜度、跨存储补偿、模型信任边界附近补简短的“为什么”
  注释。
- Schema、ORM 模型、迁移和 fixture 只说明不直观的业务约束，不逐项复述类型。

## 避免事项

- 不逐行翻译代码，例如“给 status 赋值”。
- 不把注释或 Prompt 当作访问控制；规则必须由 SQL、约束和测试执行。
- 不让注释过期。行为修改时，同一提交检查相邻注释是否还正确。
- 不在注释中写密钥、真实业务数据、私有地址或未经验证的性能结论。

Stage 7-11 调用链索引见[代码导航地图](code-navigation-map.md)。从 Stage 12
开始，每份学习文档必须包含阅读顺序、调用链、A/B/C 分级、必读/可跳过文件、
验收问题，并遵守本页的注释标准。
