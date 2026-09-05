import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

const documentStatusLabels = {
  pending: "待解析",
  processing: "处理中",
  ready: "可检索",
  failed: "处理失败",
};

async function requestApi(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  // 后端异常或开发代理失效时可能返回空内容或 HTML，不能直接调用 response.json()。
  const body = await response.text();
  let payload = null;
  if (body) {
    try {
      payload = JSON.parse(body);
    } catch {
      throw new Error(
        response.ok
          ? "后端返回了非 JSON 内容，请检查 Vite /api 代理和后端日志。"
          : `请求失败（${response.status}），后端未返回 JSON 错误信息。`,
      );
    }
  }
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail === "string" ? payload.detail : `请求失败（${response.status}）`,
    );
  }
  if (!payload && response.status !== 204) {
    throw new Error("后端返回空响应，请确认 API 已启动。");
  }
  return payload;
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) return "未知大小";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatEffectiveRange(document) {
  if (!document.effective_from && !document.effective_to) return "未设置生效范围";
  return `${document.effective_from ?? "开始未设"} 至 ${document.effective_to ?? "长期有效"}`;
}

export default function App() {
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [question, setQuestion] = useState("");
  const [expenseDate, setExpenseDate] = useState("");
  const [token, setToken] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [toolRoutingProvider, setToolRoutingProvider] = useState("");
  const [documents, setDocuments] = useState([]);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [documentActionId, setDocumentActionId] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeInfo() {
      try {
        const info = await requestApi("/api/v1/info");
        if (!cancelled) setToolRoutingProvider(info.tool_routing_provider ?? "");
      } catch {
        // 诊断标签加载失败不能影响问答、上传等主流程。
      }
    }

    void loadRuntimeInfo();
    return () => {
      cancelled = true;
    };
  }, []);

  function requestHeaders(json = false) {
    const headers = {};
    if (json) headers["Content-Type"] = "application/json";
    if (token.trim()) headers.Authorization = `Bearer ${token.trim()}`;
    return headers;
  }

  async function askAssistant(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const payload = await requestApi(
        `/api/v1/knowledge-bases/${knowledgeBaseId.trim()}/assistant`,
        {
          method: "POST",
          headers: requestHeaders(true),
          body: JSON.stringify({
            question,
            expense_date: expenseDate || null,
            top_k: 5,
          }),
        },
      );
      setResult(payload);
    } catch (requestError) {
      setError(
        requestError instanceof TypeError && requestError.message === "Failed to fetch"
          ? "无法连接后端，请确认 API 已启动且 Vite /api 代理已重启。"
          : requestError.message,
      );
    } finally {
      setLoading(false);
    }
  }

  async function refreshDocuments() {
    const kbId = knowledgeBaseId.trim();
    if (!kbId) {
      setDocumentError("请先填写知识库 ID，再查看或上传文档。");
      return;
    }
    setDocumentsLoading(true);
    setDocumentError("");
    try {
      const payload = await requestApi(`/api/v1/knowledge-bases/${kbId}/documents`, {
        headers: requestHeaders(),
      });
      setDocuments(payload);
      setDocumentsLoaded(true);
    } catch (requestError) {
      setDocumentError(requestError.message);
    } finally {
      setDocumentsLoading(false);
    }
  }

  async function uploadDocument(event) {
    event.preventDefault();
    // 跨过 await 后不要再读取事件对象，先保存表单节点用于清空文件选择。
    const form = event.currentTarget;
    const kbId = knowledgeBaseId.trim();
    if (!kbId) {
      setDocumentError("请先填写知识库 ID，再上传文档。");
      return;
    }
    if (!uploadFile) {
      setDocumentError("请选择 PDF、DOCX、Markdown 或 TXT 文件。");
      return;
    }
    setUploading(true);
    setDocumentError("");
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      await requestApi(`/api/v1/knowledge-bases/${kbId}/documents`, {
        method: "POST",
        headers: requestHeaders(),
        body: formData,
      });
      setUploadFile(null);
      form.reset();
      await refreshDocuments();
    } catch (requestError) {
      setDocumentError(requestError.message);
    } finally {
      setUploading(false);
    }
  }

  async function processAndIndexDocument(document) {
    const kbId = knowledgeBaseId.trim();
    setDocumentActionId(document.id);
    setDocumentError("");
    try {
      const basePath = `/api/v1/knowledge-bases/${kbId}/documents/${document.id}`;
      const processed = await requestApi(`${basePath}/process`, {
        method: "POST",
        headers: requestHeaders(),
      });
      if (processed.status !== "ready") {
        throw new Error(
          processed.error_message || "文档未生成可索引文本，已停止建立索引。",
        );
      }
      await requestApi(`${basePath}/embeddings`, {
        method: "POST",
        headers: requestHeaders(),
      });
      await requestApi(`${basePath}/keyword-index`, {
        method: "POST",
        headers: requestHeaders(),
      });
      await refreshDocuments();
    } catch (requestError) {
      setDocumentError(requestError.message);
      await refreshDocuments();
    } finally {
      setDocumentActionId("");
    }
  }

  async function runOcrDocument(document) {
    const kbId = knowledgeBaseId.trim();
    setDocumentActionId(document.id);
    setDocumentError("");
    try {
      const basePath = `/api/v1/knowledge-bases/${kbId}/documents/${document.id}`;
      const processed = await requestApi(`${basePath}/ocr`, {
        method: "POST",
        headers: requestHeaders(),
      });
      if (processed.status !== "ready") {
        throw new Error(processed.error_message || "OCR 未生成可索引文本。");
      }
      // OCR 会替换文档切片，因此识别完成后必须重新生成两类索引。
      await requestApi(`${basePath}/embeddings`, {
        method: "POST",
        headers: requestHeaders(),
      });
      await requestApi(`${basePath}/keyword-index`, {
        method: "POST",
        headers: requestHeaders(),
      });
      await refreshDocuments();
    } catch (requestError) {
      setDocumentError(requestError.message);
      await refreshDocuments();
    } finally {
      setDocumentActionId("");
    }
  }

  const runtimeLabel = toolRoutingProvider === "bailian"
    ? "百炼 Function Calling"
    : toolRoutingProvider === "rule"
      ? "规则路由"
      : "本地演示";

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">ENTERPRISE TRAVEL RAG</p>
          <h1>企业差旅报销助手</h1>
        </div>
        <span className="status-dot">{runtimeLabel}</span>
      </header>

      <section className="workspace">
        <form className="query-panel" onSubmit={askAssistant}>
          <label>
            知识库 ID
            <input
              value={knowledgeBaseId}
              onChange={(event) => setKnowledgeBaseId(event.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              required
            />
          </label>
          <label>
            问题
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：上海大型展会期间住宿费标准是多少？"
              rows="5"
              required
            />
          </label>
          <div className="form-row">
            <label>
              费用日期
              <input type="date" value={expenseDate} onChange={(event) => setExpenseDate(event.target.value)} />
            </label>
            <label>
              JWT（可选）
              <input type="password" value={token} onChange={(event) => setToken(event.target.value)} />
            </label>
          </div>
          <button type="submit" disabled={loading}>
            {loading ? "处理中..." : "提交问题"}
          </button>
          {error && <p className="error">{error}</p>}
        </form>

        <section className="result-panel" aria-live="polite">
          {!result && !error && <div className="empty">提交问题后，这里显示回答、引用和工具轨迹。</div>}
          {result && (
            <>
              <div className="result-heading">
                <div>
                  <p className="eyebrow">{result.route}</p>
                  <h2>{result.status}</h2>
                </div>
                <code>{result.request_id}</code>
              </div>
              <p className="answer">{result.answer}</p>
              {result.citations?.length > 0 && (
                <section className="evidence">
                  <h3>制度依据</h3>
                  {result.citations.map((citation) => (
                    <article key={citation.source_id}>
                      <strong>{citation.source_id} · {citation.original_filename}</strong>
                      <p className="citation-meta">
                        {[citation.version_label, citation.page_start ? `第 ${citation.page_start} 页` : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      <p>{citation.content}</p>
                    </article>
                  ))}
                </section>
              )}
              {result.tool_call && (
                <section className="trace-box">
                  <h3>工具轨迹</h3>
                  <p><code>{result.tool_call.tool_name}</code> · {result.tool_call.outcome}</p>
                </section>
              )}
              {result.compliance && (
                <section className="trace-box">
                  <h3>合规预检查</h3>
                  <p>{result.compliance.branch} · {result.compliance.recommendation}</p>
                  <p className="trace">{result.compliance.trace.join(" → ")}</p>
                </section>
              )}
            </>
          )}
        </section>
      </section>

      <section className="document-workspace" aria-labelledby="documents-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">KNOWLEDGE BASE</p>
            <h2 id="documents-heading">制度文档</h2>
          </div>
          <button type="button" className="secondary-button" onClick={refreshDocuments} disabled={documentsLoading}>
            {documentsLoading ? "刷新中..." : "刷新文档"}
          </button>
        </div>
        <form className="document-upload" onSubmit={uploadDocument}>
          <label>
            上传文件
            <input
              type="file"
              accept=".pdf,.docx,.md,.markdown,.txt"
              onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button type="submit" disabled={uploading}>
            {uploading ? "上传中..." : "上传文档"}
          </button>
        </form>
        {documentError && <p className="error">{documentError}</p>}
        {documentsLoaded && !documentsLoading && documents.length === 0 && (
          <p className="empty-documents">当前知识库没有文档。</p>
        )}
        {documents.length > 0 && (
          <div className="document-list">
            {documents.map((document) => {
              const processing = documentActionId === document.id;
              return (
                <article className="document-row" key={document.id}>
                  <div className="document-main">
                    <div className="document-title-row">
                      <strong>{document.original_filename}</strong>
                      <span className={`document-status status-${document.status}`}>
                        {documentStatusLabels[document.status] ?? document.status}
                      </span>
                    </div>
                    <p className="document-details">
                      {formatFileSize(document.file_size)} · {document.version_label || "未标记版本"} · {formatEffectiveRange(document)}
                    </p>
                    {document.needs_ocr && <p className="document-warning">包含待 OCR 的图片或页面</p>}
                    {document.error_message && <p className="document-warning">{document.error_message}</p>}
                  </div>
                  <div className="document-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => processAndIndexDocument(document)}
                      disabled={processing || document.status === "processing"}
                    >
                      {processing ? "处理中..." : document.status === "ready" ? "重建索引" : "解析并建索引"}
                    </button>
                    {document.needs_ocr && (
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => runOcrDocument(document)}
                        disabled={processing || document.status === "processing"}
                      >
                        {processing ? "处理中..." : "运行 OCR"}
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
