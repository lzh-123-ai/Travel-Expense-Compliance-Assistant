# Stage 8：结构化解析、可追溯切片与评测契约

## 本阶段解决什么问题

Stage 7 只证明文件可以被安全、可靠地保存；它还不能回答“文档里写了什么”。Stage 8 把已保存的 PDF、DOCX、Markdown 和 TXT 转换为稳定的结构化文本，并明确暴露未能提取的图片或扫描页，避免系统把“没读到”误说成“制度里没有”。

本阶段的数据流是：

```text
Document + storage_key
  → ParserRegistry 选择解析器
  → ParsedDocument（章节、页码、图片和提取完整性）
  → DeterministicChunker（相同输入得到相同分块）
  → document_chunks（内容、章节/页码、哈希、切片参数）
  → 未来 Stage 9 的 embedding 与检索
```

Stage 8 不做 OCR、embedding、LLM 问答或图片理解。检测到扫描内容时，系统记录 `needs_ocr` 和警告；不能提取任何文字时不生成空分块。

## 从哪里开始：推荐阅读顺序

不要先读 pypdf 内部对象，也不要一上来打开 20 题评测 JSON。建议分四遍阅读，总计约 90–120 分钟。

### 阅读前：跑一条最短主链路（5 分钟）

先运行 Markdown 解析测试：

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m pytest `
  tests/test_document_parsers.py::test_markdown_images_are_not_fetched `
  -vv
```

只记住这条契约：Markdown 标题变成 `heading_path`，图片地址不会被下载，并产生“图片未获取”的结构化警告。

### 第一遍：只追解析契约（20–25 分钟）

按顺序打开：

1. `backend/tests/test_parsing_contracts.py`
   - 看哪些非法对象会被拒绝，例如重复 section order、错误页码和互相矛盾的状态。
2. `backend/app/services/parsing/contracts.py`
   - 先看 `TextExtractionStatus`、`ParseWarning`、`ParsedSection`、`ParsedDocument`。
   - 分清业务处理状态 `pending/processing/ready/failed` 与文字提取状态 `not_attempted/complete/partial/no_text/failed`。
3. `backend/app/services/parsing/registry.py`
   - 看格式字符串怎样映射到解析器。
   - 注意注册表只负责选择，不负责数据库和 HTTP。

第一遍结束时，尝试回答：为什么解析器返回统一对象，而不是让每种格式直接写 `DocumentChunk`？

### 第二遍：按“简单到复杂”读四种解析器（25–35 分钟）

先读测试，再读对应实现：

1. `text_parser.py`：TXT 段落怎样变成 sections。
2. `text_parser.py` 中 Markdown 分支：标题层级、图片语法和外部地址边界。
3. `docx_parser.py`：段落、标题、表格如何保持文档顺序；为什么 DOCX 不制造页码。
4. `pdf_parser.py`：逐页文字、真实页码、图片计数、空白页和疑似扫描页判断。

配合阅读 `backend/tests/test_document_parsers.py`。重点跟踪四种结果：

- 原生文字 PDF：`complete`，按真实页码产生 sections。
- 正文充足但带装饰图：仍可 `complete`，同时告知图片未索引。
- 文字页与图片页混合：`partial + needs_ocr`，只给可提取文字建 section。
- 只有扫描图：`no_text + needs_ocr`，后续不生成 chunks。

这里的“图片页”只是可重复测试的启发式判断，不代表系统理解了图片内容。Markdown 远程图片不下载，DOCX 嵌入图片只计数，独立 JPG/PNG 仍在上传层被拒绝。

### 第三遍：追一次解析、切片和数据库提交（30–35 分钟）

先运行：

```powershell
..\.venv\Scripts\python.exe -m pytest `
  tests/test_document_processing.py::test_processing_replaces_chunks_and_marks_ready `
  -vv
```

然后按顺序阅读：

1. `backend/app/services/document_processing.py`
   - 从 `process` 开始，写下 `processing → parse → chunk → replace → commit`。
   - 找到解析失败、无文字、成功三条状态路径。
2. `backend/app/services/chunking.py`
   - 先看 `ChunkingConfig` 和 `ChunkDraft`。
   - 再看相邻 section 怎样合并、长文本怎样带 overlap 切开、缺失 PDF 页为什么不能跨越合并。
3. `backend/app/models/document_chunk.py`
   - 找出内容、页码、章节路径、内容哈希和切片参数。
   - 理解 `(document_id, ordinal)` 唯一约束如何构成数据库兜底。
4. `backend/app/api/routes/documents.py`
   - 看 `POST /{document_id}/process` 为什么先用 `FOR UPDATE` 锁定文档。
   - 看 `GET /{document_id}/chunks` 为什么先按知识库和文档联合查询。
5. `backend/alembic/versions/f3a1c9d7e842_add_parsing_metadata_and_chunks.py`
   - 对照 ORM，确认新增列、检查约束、外键和索引都进入真实数据库。

完整成功链路是：

```text
POST /process
  → 按 knowledge_base_id + document_id 查询并锁行
  → 标记 processing
  → 从 StorageService 打开原文件
  → ParserRegistry 选择解析器
  → 得到 ParsedDocument 和提取完整性
  → DeterministicChunker 生成可追溯 ChunkDraft
  → 删除该文档旧 chunks，再写入新 chunks
  → 标记 ready 并一次 commit
```

重新处理使用“删除旧分块 + 确定性重建”，因此不会累积历史分块；同一文档的并发处理通过数据库行锁串行执行。

### 第四遍：理解领域元数据和评测为什么现在就建（20–25 分钟）

按顺序阅读：

1. `backend/app/schemas/document.py` 中 `DocumentMetadataUpdate`。
2. `documents.py` 中 `update_document_metadata`。
3. `data/policies/manifest.json`：版本、生效期、访问范围和替代关系。
4. `backend/app/evaluation/contracts.py`：每题必须冻结哪些标注。
5. `data/evaluation/stage8_v0.json`：只选 3 题阅读：
   - `TRAVEL-EVAL-001/002`：同一问题在新旧版本下答案不同。
   - `TRAVEL-EVAL-017`：缺少日期时必须追问。
   - `TRAVEL-EVAL-019/020`：员工与财务角色的访问边界。
6. `backend/tests/test_evaluation_dataset.py`：评测文件怎样与制度 manifest 相互校验。

当前 20 题已于 2026-08-04 由项目负责人逐题对照制度原文复核，并标记为 `reviewed`。它是后续检索和问答评测的人工基线，但“答案标注已确认”不等于“系统准确率已验证”；运行真实检索/问答评测前仍不能宣传准确率。

## 第一次阅读可以暂时跳过什么

- pypdf 的 xref、图片 XObject 和 PDF 内容流细节。
- DOCX OOXML 的全部命名空间。
- SQLAlchemy relationship 的加载策略。
- Pydantic model validator 的高级写法。
- 未来 embedding、中文分词、RRF 和模型回答。

先能画出“文件 → 统一解析结果 → 分块 → 数据库”的链路，再研究库的内部实现。

## 关键设计判断

### 为什么不能把空文本当成解析成功

扫描版 PDF 可能结构完全合法，但 pypdf 只能看到图片而拿不到图片中的文字。如果返回空字符串并继续建库，未来系统可能把“没有提取到”错误解释成“制度没有规定”。因此纯扫描文件进入 `failed + no_text + needs_ocr`，不生成 chunks。

### 为什么图片计数不等于图片理解

`embedded_image_count` 只证明文档包含图片。当前没有 OCR 或视觉模型，不能知道图片是 Logo、盖章、流程图还是费用表。系统用结构化 warning 诚实暴露这一边界。

### 为什么 DOCX 不保存假页码

DOCX 是流式排版格式，页码会随字体、纸张和渲染器变化。python-docx 没有稳定的原始页码，因此当前保存标题路径；PDF 才记录真实页码。

### 为什么切片必须确定且记录参数

相同原文、相同配置应生成相同 ordinal、内容和哈希，否则 Stage 9 的检索对比无法复现。每个 chunk 保存策略名、size、overlap 和内容哈希，为后续评测说明“这次结果使用了哪套切片配置”。

### 为什么版本字段先于向量检索

“上海住宿上限是多少”可能在 2025 版和 2026 版有两个都语义相关的答案。embedding 只能找相似内容，不能替代日期、版本和权限规则；所以 Stage 8 先建立 `effective_from/effective_to`、`supersedes_document_id` 和 `access_scope`。

## 本阶段验证

- 四种格式通过统一 Parser Registry，并覆盖损坏/加密文档安全失败。
- PDF 保留真实页码，DOCX 保留标题和表格顺序但不虚构页码。
- 图片/扫描内容用 `complete/partial/no_text`、`needs_ocr` 和 warnings 明确表达。
- 长文本、overlap、缺失页、章节边界和确定性哈希有自动化测试。
- 处理失败不留下旧 chunks；重复处理结果一致；并发处理使用行锁。
- 元数据支持版本、生效期、访问范围和替代关系，且限定在同一知识库。
- 20 题评测基线具有固定 schema，经逐题人工复核并与版本化制度 manifest 对齐。
- 83 项 pytest、Ruff、格式检查、真实 Alembic upgrade/check 通过。
- 真实 Markdown 完成上传、元数据、解析、6 个分块、重复处理与清理闭环。

## 阶段任务与验收记录

### 已完成 1：按阅读顺序走通代码

完成前四遍阅读，并用自己的话画出或写出三条链路：

1. 正常 Markdown/PDF 解析与切片。
2. 混合 PDF 的部分提取与 OCR 警告。
3. 纯扫描或损坏文档的失败与旧分块清理。

### 已完成 2：人工复核 20 题评测基线

已逐题对照 `data/policies/` 原文检查问题日期、角色、制度版本、章节、答案要点和拒答/追问边界，并完成三处关键修订：第 10 题明确酒店账单的入住与离店日期；第 16 题禁止召回费用发生时尚未生效的展会通知；第 18 题增加国内适用范围作为有依据拒答的来源。`annotation_status` 已确认为 `reviewed`。

### 已完成 3：运行关键测试

```powershell
cd D:\xuexi\projects\agent_project\backend
..\.venv\Scripts\python.exe -m pytest `
  tests/test_document_parsers.py `
  tests/test_chunking.py `
  tests/test_document_processing.py `
  tests/test_evaluation_dataset.py `
  -vv
```

## 自检题

1. `Document.status` 与 `text_extraction_status` 为什么不能合并成一个字段？
2. 纯扫描 PDF、混合 PDF、带 Logo 的文字 PDF 分别应该是什么状态，是否生成 chunks？
3. 为什么 PDF 可以保存页码，而 DOCX 只保存章节路径？
4. 为什么 Markdown 解析器不下载图片 URL 或本地图片路径？
5. 为什么重新解析要删除旧 chunks，而不是直接追加？
6. `(document_id, ordinal)` 唯一约束与 `FOR UPDATE` 行锁分别解决什么问题？
7. 为什么 embedding 不能独自解决新旧制度版本冲突？
8. 为什么当前 20 题不能直接被称为正式 gold set 或用来宣传准确率？

## Stage 8 学习验收结论（2026-08-04）

- 已能沿路由、编排服务、解析器、切片器和 ORM 模型解释正常、部分提取及失败三条链路。
- 已完成八道自检题并纠正业务状态/提取状态、OCR、行锁和版本过滤等概念。
- 20 题评测基线已逐题人工确认，评测数据测试通过。
- Stage 8 学习验收通过。下一步先创建 Stage 8 独立提交，再按最新交接文档进入 Stage 9。
