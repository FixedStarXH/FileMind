import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowUp, BookOpenText, Check, CircleAlert, FileText, LoaderCircle, MessageSquareText, Plus, Square, X } from "lucide-react";
import { ApiError, request } from "./api";
import "./Assistant.css";

type Settings = { ai_enabled: boolean; capabilities: { deepseek: boolean } };
type Scope = { document_ids: string[]; folder_ids: string[]; tag_ids: string[]; inbox: boolean; all_documents: boolean };
type Folder = { id: string; name: string; parent_id: string | null };
type Tag = { id: string; name: string; color: string };
type Conversation = { id: string; title: string; scope: Scope; revision: number };
type Message = { id: string; role: "user" | "assistant"; content: string; run_id: string | null; redacted: boolean };
type Citation = { id: string; document_id: string; version_id: string; title?: string; locator: Record<string, unknown>; quote: string; status: string; historical_version?: boolean };
type RunDetail = { id: string; status: string; answer: string | null; citations: Citation[]; error: string | null; degraded: boolean };
type StreamPayload = { run_id: string; seq: number; text?: string; message?: string; stage?: string; id?: string; title?: string; document_id?: string; version_id?: string; chunk_id?: string; locator?: Record<string, unknown>; quote?: string; snapshot_url?: string; redacted?: boolean };

const blankScope: Scope = { document_ids: [], folder_ids: [], tag_ids: [], inbox: false, all_documents: false };
const failureText: Record<string, string> = {
  AI_NOT_CONFIGURED: "管理员尚未配置 DeepSeek API Key。资料库和本地检索仍可使用。",
  AI_DISABLED: "AI 问答未启用。请到账户设置中启用后再试。",
  MODEL_AUTH_FAILED: "DeepSeek Key 无效或已失效，请检查服务端配置。",
  MODEL_REQUEST_REJECTED: "DeepSeek 拒绝了本次请求，请稍后重试。",
  MODEL_RESPONSE_INVALID: "模型返回的内容无法通过引用校验，请重新提问。",
  SOURCE_CHANGED: "资料在生成答案时发生变化，请重新提问。",
  TASK_EXECUTION_FAILED: "问答任务暂时失败，请重新尝试。",
  RATE_LIMITED: "问答次数或并行任务达到上限，请稍后重试。",
};

function scopeValue(scope: Scope): string {
  if (scope.all_documents) return "all";
  if (scope.inbox) return "inbox";
  if (scope.folder_ids.length === 1) return `folder:${scope.folder_ids[0]}`;
  if (scope.tag_ids.length === 1) return `tag:${scope.tag_ids[0]}`;
  return "";
}

function locatorLabel(locator: Record<string, unknown>): string {
  const source = (locator.source && typeof locator.source === "object" ? locator.source : locator) as Record<string, unknown>;
  if (source.page) return `第 ${source.page} 页`;
  if (source.paragraph) return `第 ${source.paragraph} 段`;
  if (source.slide) return `第 ${source.slide} 张幻灯片`;
  if (source.line) return `第 ${source.line} 行`;
  if (source.table) return `表格 ${source.table}`;
  return "文档片段";
}

export default function Assistant({
  userEmail, settings, csrf, onLibrary, onOpenCitation, onSettings, onLogout,
}: {
  userEmail: string; settings: Settings; csrf: string; onLibrary: () => void; onOpenCitation: (citation: Citation) => void; onSettings: () => void; onLogout: () => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Record<string, RunDetail>>({});
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [scopeChoice, setScopeChoice] = useState("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeRunId, setActiveRunId] = useState("");
  const [streamText, setStreamText] = useState("");
  const [streamCitations, setStreamCitations] = useState<Citation[]>([]);
  const [progress, setProgress] = useState("就绪，等待你的问题");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const selectedScope = useMemo<Scope | null>(() => {
    if (scopeChoice === "all") return { ...blankScope, all_documents: true };
    if (scopeChoice === "inbox") return { ...blankScope, inbox: true };
    if (scopeChoice.startsWith("folder:")) return { ...blankScope, folder_ids: [scopeChoice.slice(7)] };
    if (scopeChoice.startsWith("tag:")) return { ...blankScope, tag_ids: [scopeChoice.slice(4)] };
    return null;
  }, [scopeChoice]);

  async function refreshConversations() {
    const result = await request<{ items: Conversation[] }>("/conversations");
    setConversations(result.data.items);
  }

  useEffect(() => {
    void Promise.all([
      refreshConversations(),
      request<{ items: Folder[] }>("/folders").then((result) => setFolders(result.data.items)),
      request<{ items: Tag[] }>("/tags").then((result) => setTags(result.data.items)),
    ]).catch(() => setError("无法读取对话或资料范围，请刷新后重试。"));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamText, progress]);

  async function loadConversation(id: string) {
    setError("");
    setNotice("");
    setActiveRunId("");
    setStreamText("");
    setStreamCitations([]);
    try {
      const result = await request<{ conversation: Conversation; messages: Message[] }>(`/conversations/${id}`);
      setConversationId(id);
      setScopeChoice(scopeValue(result.data.conversation.scope));
      setMessages(result.data.messages);
      const runIds = [...new Set(result.data.messages.flatMap((item) => item.role === "assistant" && item.run_id ? [item.run_id] : []))];
      const details = await Promise.all(runIds.map(async (runId) => {
        const run = await request<RunDetail>(`/agent/runs/${runId}`);
        return [runId, run.data] as const;
      }));
      setRuns(Object.fromEntries(details));
    } catch {
      setError("无法打开这段对话。它可能已被删除，或当前账户无权访问。");
    }
  }

  function startNewConversation() {
    setConversationId("");
    setMessages([]);
    setRuns({});
    setStreamText("");
    setStreamCitations([]);
    setActiveRunId("");
    setBusy(false);
    setError("");
    setNotice("");
    setProgress("选择资料范围后开始提问");
  }

  async function loadRun(id: string) {
    try {
      const result = await request<RunDetail>(`/agent/runs/${id}`);
      setRuns((previous) => ({ ...previous, [id]: result.data }));
      if (result.data.answer !== null || result.data.error === "SOURCE_REMOVED") {
        setMessages((previous) => {
          const index = previous.findIndex((item) => item.role === "assistant" && item.run_id === id);
          const assistantMessage: Message = {
            id: `assistant-${id}`, role: "assistant", content: result.data.answer ?? "",
            run_id: id, redacted: result.data.error === "SOURCE_REMOVED",
          };
          if (index >= 0) return previous.map((item, itemIndex) => itemIndex === index ? assistantMessage : item);
          return [...previous, assistantMessage];
        });
      }
      if (result.data.error) setError(failureText[result.data.error] ?? "问答任务失败，请重新尝试。");
      if (result.data.degraded) setNotice("本次语义索引不可用，答案使用了关键词召回结果。");
    } catch {
      setError("无法读取本次问答结果。");
    }
  }

  useEffect(() => {
    if (!activeRunId) return;
    const source = new EventSource(`/api/v1/agent/runs/${activeRunId}/events`, { withCredentials: true });
    const handle = (event: Event) => {
      const payload = JSON.parse((event as MessageEvent<string>).data) as StreamPayload;
      if (payload.message) setProgress(payload.message);
      if (payload.text) setStreamText((previous) => previous + payload.text);
      if (payload.id && payload.document_id && payload.locator) {
        setStreamCitations((previous) => previous.some((item) => item.id === payload.id) ? previous : [...previous, {
          id: payload.id!, document_id: payload.document_id!, version_id: payload.version_id ?? "",
          title: payload.title, locator: payload.locator!, quote: payload.quote ?? "", status: "available",
        }]);
      }
      if (payload.redacted) setNotice("引用来源已删除，历史回答内容已清理。");
      if ((event as MessageEvent).type === "stream.reset") {
        source.close();
        void loadRun(activeRunId);
        setBusy(false);
        setActiveRunId("");
      }
      if (["run.completed", "run.failed", "run.cancelled", "run.expired"].includes((event as MessageEvent).type)) {
        source.close();
        void loadRun(activeRunId);
        void refreshConversations();
        setBusy(false);
        setActiveRunId("");
      }
    };
    for (const name of ["run.started", "run.step", "answer.delta", "citation.added", "run.completed", "run.failed", "run.cancelled", "run.expired", "stream.reset"]) {
      source.addEventListener(name, handle);
    }
    source.onerror = () => setProgress("连接中断，浏览器正在尝试续传…");
    return () => source.close();
  }, [activeRunId]);

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt || !selectedScope || busy) return;
    if (!settings.ai_enabled) {
      setError(failureText.AI_DISABLED);
      return;
    }
    if (!settings.capabilities.deepseek) {
      setError(failureText.AI_NOT_CONFIGURED);
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    setStreamText("");
    setStreamCitations([]);
    setProgress("正在提交问题…");
    try {
      let currentConversationId = conversationId;
      if (!currentConversationId) {
        const created = await request<Conversation>("/conversations", {
          method: "POST",
          headers: { "X-CSRF-Token": csrf },
          body: JSON.stringify({ title: prompt.slice(0, 80), scope: selectedScope }),
        });
        currentConversationId = created.data.id;
        setConversationId(currentConversationId);
        await refreshConversations();
      }
      const result = await request<{ run_id: string }>("/agent/runs", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body: JSON.stringify({ mode: "qa", conversation_id: currentConversationId, instruction: prompt, scope: selectedScope }),
      });
      setMessages((previous) => [...previous, { id: crypto.randomUUID(), role: "user", content: prompt, run_id: result.data.run_id, redacted: false }]);
      setQuestion("");
      setActiveRunId(result.data.run_id);
      setProgress("问答任务已排队，正在准备资料证据…");
    } catch (cause) {
      const code = cause instanceof ApiError ? cause.message : "";
      setError(failureText[code] ?? "问题提交失败，请稍后重试。");
      setBusy(false);
    }
  }

  async function cancelActiveRun() {
    if (!activeRunId) return;
    try {
      await request(`/agent/runs/${activeRunId}/cancel`, { method: "POST", headers: { "X-CSRF-Token": csrf } });
      setProgress("正在停止问答…");
    } catch {
      setError("无法停止本次问答，请稍后检查任务中心。");
    }
  }

  const renderCitation = (citation: Citation) => (
    <article className={`assistant-citation ${citation.status !== "available" ? "is-muted" : ""}`} key={`${citation.id}-${citation.document_id}`}>
      <div className="citation-index">{citation.id}</div>
      <div className="citation-body">
        <div className="citation-heading"><strong>{citation.title ?? "资料来源"}</strong><span>{locatorLabel(citation.locator)}{citation.historical_version ? " · 历史版本" : ""}</span></div>
        <p>{citation.status === "available" ? citation.quote : citation.status === "trashed" ? "来源已移入回收站。" : "来源已移除。"}</p>
        {citation.status === "available" && <button className="citation-open" onClick={() => onOpenCitation(citation)}>打开原文</button>}
      </div>
    </article>
  );

  return (
    <main className="assistant-shell">
      <aside className="assistant-sidebar">
        <button className="assistant-back" onClick={onLibrary}><ArrowLeft size={16} />返回资料库</button>
        <div className="assistant-mark"><span><BookOpenText size={20} /></span><div><strong>知档问答</strong><small>基于你的学习与求职资料</small></div></div>
        <button className="assistant-new" onClick={startNewConversation}><Plus size={16} />新建对话</button>
        <div className="assistant-history-label">最近对话 <span>{conversations.length}</span></div>
        <nav className="assistant-history" aria-label="最近对话">
          {conversations.map((item) => <button className={conversationId === item.id ? "is-active" : ""} key={item.id} onClick={() => void loadConversation(item.id)}><MessageSquareText size={15} /><span>{item.title}</span></button>)}
          {conversations.length === 0 && <p>你的问答记录会保存在这里。</p>}
        </nav>
        <div className="assistant-profile"><span>{userEmail.slice(0, 1).toUpperCase()}</span><div><strong>{userEmail}</strong><small>私人资料空间</small></div><button onClick={onSettings} aria-label="账户设置"><BookOpenText size={15} /></button><button onClick={onLogout} aria-label="退出登录"><X size={15} /></button></div>
      </aside>

      <section className="assistant-main">
        <header className="assistant-topbar"><div><span>FILEMIND / RESEARCH DESK</span><strong>{conversationId ? conversations.find((item) => item.id === conversationId)?.title ?? "问答" : "新建研究问题"}</strong></div><div className="assistant-state"><i className={busy ? "is-working" : ""} />{busy ? progress : "资料范围由你决定"}</div></header>
        <div className="assistant-content">
          {messages.length === 0 && !busy ? (
            <section className="assistant-welcome">
              <div className="assistant-eyebrow">个人资料 · 有据可查</div>
              <h1>让资料为你<br /><em>解释自己。</em></h1>
              <p>围绕课件、论文、证书和求职材料提问。每个事实答案都会附上来源片段与文档位置。</p>
              <div className="assistant-example"><span>试着问</span><button onClick={() => setQuestion("根据我的资料，TCP/IP 的传输层主要负责什么？")}>根据我的资料，TCP/IP 的传输层主要负责什么？<ArrowUp size={14} /></button></div>
            </section>
          ) : (
            <div className="assistant-thread">
              {messages.map((message) => {
                const detail = message.run_id ? runs[message.run_id] : undefined;
                return <article className={`assistant-message ${message.role}`} key={message.id}>
                  <div className="message-role">{message.role === "user" ? "你" : <><BookOpenText size={14} />知档</>}</div>
                  <div className="message-content">{message.redacted ? "（相关来源已移除，历史回答内容已清理）" : message.content}</div>
                  {message.role === "assistant" && detail?.citations?.length ? <div className="citation-list">{detail.citations.map(renderCitation)}</div> : null}
                  {message.role === "assistant" && detail?.degraded && <div className="assistant-notice">语义检索暂不可用，本次使用关键词检索。</div>}
                </article>;
              })}
              {busy && <article className="assistant-message assistant live-answer"><div className="message-role"><BookOpenText size={14} />知档</div><div className="message-content">{streamText || <span className="thinking-line"><LoaderCircle className="spin" size={15} />{progress}</span>}</div>{streamCitations.length > 0 && <div className="citation-list">{streamCitations.map(renderCitation)}</div>}</article>}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <footer className="assistant-compose-wrap">
          {error && <div className="assistant-error"><CircleAlert size={15} />{error}<button onClick={() => setError("")} aria-label="关闭错误"><X size={14} /></button></div>}
          {notice && <div className="assistant-notice"><Check size={14} />{notice}</div>}
          {!settings.capabilities.deepseek && <div className="assistant-key-hint"><CircleAlert size={15} />管理员尚未配置 DeepSeek API Key，AI 问答目前不可用。</div>}
          <form className="assistant-compose" onSubmit={(event) => void submitQuestion(event)}>
            <label className="assistant-scope"><BookOpenText size={15} /><select value={scopeChoice} onChange={(event) => setScopeChoice(event.target.value)} aria-label="选择资料范围"><option value="">先选择资料范围…</option><option value="all">全部资料（明确授权）</option><option value="inbox">收件箱</option>{folders.map((folder) => <option value={`folder:${folder.id}`} key={folder.id}>文件夹：{folder.name}</option>)}{tags.map((tag) => <option value={`tag:${tag.id}`} key={tag.id}>标签：{tag.name}</option>)}</select></label>
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={selectedScope ? "问一个关于所选资料的问题…" : "先选择资料范围，再输入问题"} maxLength={4000} aria-label="你的问题" disabled={busy || !selectedScope} />
            <div className="assistant-compose-bottom"><span>所选资料片段会发送至 DeepSeek；OCR 与向量检索在本地运行。</span>{busy ? <button className="assistant-stop" type="button" onClick={() => void cancelActiveRun()}><Square size={13} />停止</button> : <button className="assistant-send" type="submit" aria-label="发送问题" disabled={!selectedScope || !question.trim() || !settings.ai_enabled || !settings.capabilities.deepseek}><ArrowUp size={17} /></button>}</div>
          </form>
          <div className="assistant-footer-note">答案仅依据可引用资料生成 · 无证据时会说明无法回答</div>
        </footer>
      </section>
    </main>
  );
}
