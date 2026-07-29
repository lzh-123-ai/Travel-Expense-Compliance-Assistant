# Stage 7：可靠文档摄取

## 本阶段解决什么问题

上传接口不能只把客户端发送的字节写到磁盘。客户端文件名和 MIME 可以伪造，写文件或提交数据库也可能只成功一半，两个同时上传的相同文件还可能绕过应用层预检查。

Stage 7 将上传链路拆成三个职责：

```text
documents route 负责流程与 HTTP 语义
       │
       ├─ document_validation 负责格式契约
       ├─ StorageService 负责原子落盘、哈希与删除补偿
       └─ PostgreSQL 负责元数据和并发唯一约束
```

## 从哪里开始：推荐阅读顺序

不要从项目目录第一个文件开始逐行阅读，也不要第一次就试图理解 45 个测试。先追踪“一次正常 TXT 上传”，再逐步加入失败路径。建议分三遍完成，总计约 60–90 分钟。

### 阅读前：先建立一个可观察的起点（5 分钟）

先单独运行最简单的成功测试：

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m pytest `
  tests/test_upload_documents.py::test_upload_document_persists_file_hash_and_safe_metadata `
  -vv
```

此时只需要记住测试表达的契约：上传 `policy.txt` 后返回 `201`，响应包含安全元数据和 SHA-256，文件以服务端生成的 key 保存。暂时不用研究 `AsyncMock` 是怎么工作的。

### 第一遍：只追正常上传主链路（25–35 分钟）

按下面顺序打开文件；看完一个再进入下一个：

1. `backend/tests/test_upload_documents.py`
   - 只看 `test_upload_document_persists_file_hash_and_safe_metadata`。
   - 找出输入、期望 HTTP 响应、数据库对象和磁盘文件四项断言。
2. `backend/app/api/routes/documents.py`
   - 从 `upload_document` 开始，不要先读顶部所有辅助函数。
   - 在纸上或笔记中写出它依次调用了谁。
3. `backend/app/services/document_validation.py`
   - 先看 `FORMAT_RULES` 和 `validate_upload_metadata`。
   - 再看 `validate_stored_content` 如何把不同格式分派给三个私有校验函数。
4. `backend/app/services/storage.py`
   - 先看 `StorageService` 协议规定了哪些能力。
   - 再只看 `LocalStorage.save`：临时文件、分块读取、大小累计、哈希计算、原子落盘。
5. `backend/app/models/document.py`
   - 找到 `storage_key`、`sha256` 和知识库内哈希唯一索引。
6. `backend/app/schemas/document.py`
   - 对比 ORM 字段与 API 响应字段，确认为什么没有 `storage_key`。
7. `backend/alembic/versions/bd1fd867ba63_harden_document_ingestion.py`
   - 查看旧 `storage_path` 怎样迁移成相对 `storage_key`，以及唯一索引怎样落到真实数据库。

正常上传的完整调用链是：

```text
POST multipart file
  → upload_document
  → 确认 knowledge base 存在
  → validate_upload_metadata（文件名、扩展名、MIME）
  → LocalStorage.save（分块写入、限制大小、计算 SHA-256）
  → validate_stored_content（检查真实内容）
  → 查询同知识库是否已有相同 SHA-256
  → 创建 Document 并 commit
  → refresh 数据库生成字段
  → DocumentResponse 过滤内部字段后返回 201
```

第一遍结束时，你只需能用自己的话复述这条链路。不要求记住每个 import，也不要求理解 ZIP 的每个 XML 条目。

### 第二遍：看失败清理和重复竞争（20–25 分钟）

回到 `backend/tests/test_upload_documents.py`，按顺序看：

1. `test_upload_document_rejects_oversized_file_and_cleans_up`
2. `test_upload_document_rejects_extension_and_mime_mismatch`
3. `test_upload_document_rejects_fake_pdf_and_removes_it`
4. `test_upload_document_returns_existing_id_for_duplicate_content`
5. `test_upload_document_handles_concurrent_duplicate_constraint`

每看一个测试，就回到 `upload_document` 找对应的 `except` 或分支。重点回答：错误发生时，临时文件、正式文件和数据库事务分别处于什么状态？

随后阅读：

- `backend/tests/test_storage.py`：理解原子保存、超限清理和路径逃逸。
- `backend/tests/test_document_validation.py`：理解四种格式如何验证，以及通用 ZIP 为什么不能冒充 DOCX。

第二遍暂时忽略 fixture 中生成 PDF xref 和 DOCX XML 的细节；它们只是构造可重复的真实格式样本，不是 Stage 7 的业务重点。

### 第三遍：看删除补偿和查询边界（15–20 分钟）

先读 `backend/tests/test_manage_documents.py` 中的四个测试，再回到路由：

1. `list_documents`：为什么先确认知识库存在？
2. `_get_document`：为什么同时使用 `document_id` 和 `knowledge_base_id` 查询？
3. `delete_document`：依次找到 `quarantine → session.delete → commit → purge`。
4. 提交失败测试：找到 `rollback → restore` 的逆向路径。

最后再看 `LocalStorage.quarantine`、`restore` 和 `purge`。到这里才需要理解为什么文件系统操作不能被 PostgreSQL 事务自动回滚。

### 第四步：用调试器亲自走一次（可选，15 分钟）

如果使用 PyCharm，在以下位置依次打断点，然后 Debug 第一条成功测试：

1. `upload_document` 入口。
2. `validate_upload_metadata`。
3. `LocalStorage.save` 的读取循环。
4. `validate_stored_content`。
5. `_find_duplicate_id`。
6. `session.commit()` 前一行。

每次暂停只观察五个值：`knowledge_base_id`、原始文件名、`storage_key`、累计 `size`、最终 `sha256`。不要一次展开整个 Session 或 FastAPI 对象。

### 第一次阅读可以暂时跳过什么

- `Protocol`、`Annotated` 和依赖注入的完整类型原理。
- PDF xref、DOCX OPC/XML 的具体格式规范。
- `AsyncMock` 的全部行为。
- Alembic 环境文件中的异步桥接细节。
- 日志系统和未来对象存储实现。

先建立主链路，再研究这些细节。阅读代码的目标不是把每一行都翻译成中文，而是能说清数据怎样流动、失败怎样恢复、边界由谁负责。

## 关键设计

### 为什么 MIME 不能只信请求头

`UploadFile.content_type` 来自客户端。将 TXT 改名成 PDF，并把请求头填成 `application/pdf`，服务端仍会收到看似正确的 MIME。因此当前同时检查：

- 允许的扩展名。
- 扩展名对应的 MIME 白名单。
- PDF 文件头/结束标记。
- DOCX 的 ZIP 结构和必要 Word 条目。
- Markdown/TXT 是否为非空 UTF-8 文本且不含二进制空字节。

这仍不是完整恶意文件扫描。Stage 8 使用解析器时还要限制解析资源、捕获损坏文档，并把失败状态写入数据库。

### 为什么存 storage key 而不是绝对路径

数据库保存 `documents/{uuid}.ext`，而不是某台开发机的 `D:\...\uploads`。StorageService 决定如何把 key 映射到具体位置，因此未来切换对象存储时业务表和 API 不依赖机器目录。

原始文件名只作展示元数据。真实 key 使用服务端 UUID，且 LocalStorage 会拒绝逃逸存储根目录的 `../` 路径。

### 为什么哈希需要数据库唯一索引

应用先查询 SHA-256 可以返回友好的 `409`，但两个并发请求可能同时得到“不重复”。数据库中的 `(knowledge_base_id, sha256)` 部分唯一索引才是最终约束。发生竞争时应用回滚、删除本次文件，再查询并返回已有文档 ID。

同一内容允许存在于不同知识库，因为未来它们可能有不同权限和生命周期。

### 为什么删除先隔离文件

文件系统和 PostgreSQL 不能参与同一个事务：

1. 先删文件、数据库提交失败，会留下指向不存在文件的记录。
2. 先提交数据库、之后文件删除失败，会产生孤儿文件。

当前实现先把文件原子移动到 `.trash`，再删除数据库记录：

- 数据库失败时，把文件移回原处。
- 数据库成功后，清理隔离文件。
- 最后清理失败只记录错误，因为业务记录已经成功删除；后续阶段可增加定期垃圾清理。

## API 语义

- `415`：扩展名不支持，或声明 MIME 与扩展名不匹配。
- `422`：扩展名/MIME 合法，但文件实际结构无效、为空或编码错误。
- `413`：上传超过压缩文件大小限制。
- `409`：同一知识库已有相同 SHA-256 的文档，并返回已有文档 ID。
- `204`：文档和本地文件删除成功。

## 本阶段验证

- PDF、DOCX、Markdown、TXT 都通过完整上传 API。
- 覆盖伪装 PDF、通用 ZIP 冒充 DOCX、空/二进制/非 UTF-8 文本。
- 覆盖超限清理、重复预检查和并发唯一约束冲突。
- 覆盖本地路径逃逸。
- 覆盖删除成功，以及数据库提交失败后的文件恢复。
- Ruff、pytest、真实 Alembic upgrade/check 通过。

## 自检题

1. 为什么扩展名、MIME 和文件内容三者要一起检查？
2. 为什么应用层先查重复后，仍必须建立数据库唯一索引？
3. 为什么同一文件可以存在于不同知识库？
4. 为什么删除文件与数据库记录不能获得普通 SQL 事务的原子性？
5. 如果数据库提交成功但隔离文件清理失败，接口应该返回成功还是失败？为什么？
6. 当前 DOCX 校验能防止什么，仍不能防止什么？
