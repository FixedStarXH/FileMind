import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ArrowLeft, Archive, BriefcaseBusiness, Check, CheckCircle2, Download, FileText, LoaderCircle, PackageCheck, RefreshCw, ShieldCheck, Sparkles, X } from "lucide-react";
import { request, type ApiError } from "./api";
import "./ApplicationPacks.css";
import "./ApplicationPacksSources.css";

type Folder = { id: string; name: string };
type Tag = { id: string; name: string };
type DocumentItem = { id: string; title: string; current_version_id: string | null; original_name: string | null; size_bytes: number; parse_state: string | null; mime: string | null };
type Source = { id: string; document_id: string; title: string; locator: Record<string, unknown>; excerpt: string };
type Action = { id: string; type: string; document_id: string; version_id: string | null; before: Record<string, unknown>; after: Record<string, unknown>; reason: string; citation_ids: string[]; sources?: Source[] };
type Plan = { id: string; plan_hash: string; status: string; expires_at: string; actions: Action[]; missing_items: string[] };
type Run = { id: string; status: string; plan_id: string | null; answer: string | null; error: string | null };
type Export = { id: string; state: string; size_bytes: number | null; expires_at: string; error_code: string | null; download_url: string | null; task: { status: string; progress: { completed: number; total: number } | null } | null };
type Props = { csrf: string; aiEnabled: boolean; hasDeepSeek: boolean; onLibrary: () => void; onSettings: () => void; onLogout: () => void };

const errors: Record<string, string> = {
  AI_NOT_CONFIGURED: "管理员尚未配置 DeepSeek API Key。",
  AI_DISABLED: "请先在账户设置中启用 AI。",
  STALE_PLAN: "资料在方案生成后发生了变化，请重新生成。",
  QUOTA_EXCEEDED: "账户剩余空间不足以生成资料包。",
  EXPORT_TOO_LARGE: "所选材料超过资料包大小限制。",
  RATE_LIMITED: "已有导出任务正在处理，请稍后重试。",
  SOURCE_UNAVAILABLE: "部分源文件已不可用，请重新选择材料。",
};

function explain(cause: unknown) {
  const code = (cause as ApiError)?.message ?? "";
  return errors[code] ?? "操作失败，请稍后重试。";
}

function sizeLabel(value: number) {
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.ceil(value / 1024))} KB`;
}

function scopeValue(scope: string) {
  if (scope === "inbox") return { document_ids: [], folder_ids: [], tag_ids: [], inbox: true, all_documents: false };
  if (scope.startsWith("folder:")) return { document_ids: [], folder_ids: [scope.slice(7)], tag_ids: [], inbox: false, all_documents: false };
  return { document_ids: [], folder_ids: [], tag_ids: [scope.slice(4)], inbox: false, all_documents: false };
}

export default function ApplicationPacks({ csrf, aiEnabled, hasDeepSeek, onLibrary, onSettings, onLogout }: Props) {
  const [tab, setTab] = useState<"agent" | "manual">("agent");
  const [scope, setScope] = useState("inbox");
  const [instruction, setInstruction] = useState("为后端开发实习岗位准备一份求职资料包，优先选择能证明项目经验、技能和教育背景的材料，并指出我还缺少什么。");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [manualSelected, setManualSelected] = useState<string[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [selectedActions, setSelectedActions] = useState<string[]>([]);
  const [exports, setExports] = useState<Export[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const folderNames = useMemo(() => new Map(folders.map((item) => [item.id, item.name])), [folders]);

  async function loadExports() {
    const result = await request<{ items: Export[] }>("/exports?limit=50");
    setExports(result.data.items);
  }

  useEffect(() => {
    void Promise.all([
      request<{ items: Folder[] }>("/folders"),
      request<{ items: Tag[] }>("/tags"),
      request<{ items: DocumentItem[] }>("/documents?state=active&limit=100"),
      loadExports(),
    ]).then(([folderResult, tagResult, documentResult]) => {
      setFolders(folderResult.data.items);
      setTags(tagResult.data.items);
      setDocuments(documentResult.data.items);
    }).catch((cause) => setError(explain(cause)));
  }, []);

  useEffect(() => {
    if (!runId || !["queued", "running"].includes(run?.status ?? "")) return;
    let live = true;
    const refresh = async () => {
      try {
        const result = await request<Run>(`/agent/runs/${runId}`);
        if (!live) return;
        setRun(result.data);
        if (result.data.plan_id) {
          const planResult = await request<Plan>(`/agent/runs/${runId}/plan`);
          if (!live) return;
          setPlan(planResult.data);
          setSelectedActions(planResult.data.actions.map((action) => action.id));
        }
        if (result.data.status === "succeeded") void loadExports();
      } catch { /* transient; poll again */ }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1800);
    return () => { live = false; window.clearInterval(timer); };
  }, [runId, run?.status]);

  const hasActiveExports = exports.some((item) => item.state === "queued" || item.state === "running");
  useEffect(() => {
    if (!hasActiveExports) return;
    let live = true;
    const timer = window.setInterval(() => {
      void request<{ items: Export[] }>("/exports?limit=50")
        .then((result) => { if (live) setExports(result.data.items); })
        .catch(() => undefined);
    }, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [hasActiveExports]);

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setNotice(""); setPlan(null); setRun(null); setRunId("");
    try {
      const result = await request<{ run_id: string }>("/agent/runs", {
        method: "POST", headers: { "X-CSRF-Token": csrf },
        body: JSON.stringify({ mode: "application_pack", instruction, scope: scopeValue(scope) }),
      });
      setRunId(result.data.run_id);
      setRun({ id: result.data.run_id, status: "queued", plan_id: null, answer: null, error: null });
    } catch (cause) { setError(explain(cause)); }
    finally { setBusy(false); }
  }

  async function decide(decision: "approve" | "reject") {
    if (!plan || !runId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await request<{ status: string }>(`/agent/runs/${runId}/approval`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ plan_id: plan.id, plan_hash: plan.plan_hash, decision, selected_action_ids: decision === "approve" ? selectedActions : [] }),
      });
      setRun((current) => current ? { ...current, status: result.data.status } : current);
      setNotice(decision === "approve" ? "已确认所选材料，资料包任务开始处理。" : "已拒绝方案，没有创建资料包。");
      if (decision === "reject") setPlan((current) => current ? { ...current, status: "rejected" } : current);
    } catch (cause) { setError(explain(cause)); }
    finally { setBusy(false); }
  }

  async function createManualExport() {
    const versionIds = documents.filter((item) => manualSelected.includes(item.id) && item.current_version_id).map((item) => item.current_version_id as string);
    if (!versionIds.length) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await request("/exports", { method: "POST", headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ version_ids: versionIds }) });
      setNotice("导出任务已创建，可在下方查看进度。"); setManualSelected([]); await loadExports();
    } catch (cause) { setError(explain(cause)); }
    finally { setBusy(false); }
  }

  async function refreshExports() {
    setBusy(true);
    try { await loadExports(); setNotice("导出状态已更新。"); }
    catch (cause) { setError(explain(cause)); }
    finally { setBusy(false); }
  }

  const readyDocuments = documents.filter((item) => item.current_version_id && item.parse_state === "ready");
  const awaiting = run?.status === "awaiting_approval" && plan?.status === "awaiting_approval";
  const active = ["queued", "running"].includes(run?.status ?? "");

  return <main className="packs-shell">
    <header className="packs-topbar"><button className="packs-back" onClick={onLibrary}><ArrowLeft size={17} />返回资料库</button><div className="packs-brand"><span><BriefcaseBusiness size={16} /></span><strong>求职资料包</strong><small>材料清单 · 缺项提示 · 安全导出</small></div><div className="packs-account"><button onClick={onSettings}>设置</button><button onClick={onLogout}>退出</button></div></header>
    <section className="packs-content">
      <div className="packs-intro"><div className="packs-kicker">CAREER MATERIALS · PRIVATE WORKSPACE</div><h1>把求职材料，<em>整理成有据可查的一包。</em></h1><p>Agent 只推荐你已上传且完成识别的材料。你检查用途与缺项并确认后，系统才会生成 ZIP；不会替你编造经历或发送给招聘方。</p></div>
      <div className="packs-tabs" role="tablist"><button className={tab === "agent" ? "active" : ""} onClick={() => setTab("agent")}><Sparkles size={16} />智能资料包</button><button className={tab === "manual" ? "active" : ""} onClick={() => setTab("manual")}><Archive size={16} />手动导出</button></div>
      {!hasDeepSeek && tab === "agent" && <div className="packs-warning"><ShieldCheck size={16} /><span>智能分析需要部署管理员在服务端配置 DeepSeek API Key。你仍可使用手动导出打包已识别的资料。</span><button type="button" onClick={() => setTab("manual")}>使用手动导出</button></div>}
      {hasDeepSeek && !aiEnabled && tab === "agent" && <div className="packs-warning"><ShieldCheck size={16} />请先在账户设置中启用 AI。<button onClick={onSettings}>打开设置</button></div>}
      {error && <div className="packs-error" role="alert">{error}<button onClick={() => setError("")} aria-label="关闭"><X size={15} /></button></div>}
      {notice && <div className="packs-notice" role="status"><CheckCircle2 size={16} />{notice}</div>}

      {tab === "agent" && <>
        {!runId && <form className="packs-form" onSubmit={(event) => void createPlan(event)}>
          <label><span>资料范围</span><select value={scope} onChange={(event) => setScope(event.target.value)}><option value="inbox">收件箱</option>{folders.map((folder) => <option key={folder.id} value={`folder:${folder.id}`}>文件夹 · {folder.name}</option>)}{tags.map((tag) => <option key={tag.id} value={`tag:${tag.id}`}>标签 · {tag.name}</option>)}</select></label>
          <label><span>目标岗位与材料要求</span><textarea value={instruction} maxLength={4000} onChange={(event) => setInstruction(event.target.value)} /></label>
          <div className="packs-form-foot"><span><ShieldCheck size={14} />只将所选资料的相关文本片段发送到 DeepSeek；生成和下载都需你确认。</span><button className="packs-primary" disabled={busy || !hasDeepSeek || !aiEnabled || !instruction.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}分析资料</button></div>
        </form>}
        {active && <div className="packs-progress"><LoaderCircle className="spin" size={20} /><div><strong>正在核对材料和证据</strong><span>完成后会展示推荐清单与缺项，原文件不会改变。</span></div></div>}
        {plan && <section className="packs-plan"><div className="packs-plan-head"><div><div className="packs-kicker">REVIEW BEFORE EXPORT</div><h2>材料清单</h2><p>每份推荐都关联到所选资料的识别证据。取消勾选的文件不会加入 ZIP。</p></div><span className={`packs-state state-${plan.status}`}>{awaiting ? "等待确认" : plan.status === "approved" ? "正在打包" : plan.status === "completed" ? "已完成" : plan.status === "rejected" ? "已拒绝" : plan.status}</span></div>
          {plan.actions.length > 0 ? <div className="packs-material-list">{plan.actions.map((action, index) => <article className="packs-material" key={action.id}><div className="packs-material-check"><input type="checkbox" aria-label={`选择${String(action.after.title ?? "材料")}`} checked={selectedActions.includes(action.id)} disabled={!awaiting} onChange={() => setSelectedActions((current) => current.includes(action.id) ? current.filter((id) => id !== action.id) : [...current, action.id])} /></div><div className="packs-file-icon"><FileText size={19} /></div><div className="packs-material-main"><div className="packs-material-title"><strong>{String(action.after.title ?? "资料")}</strong><span>{String(action.after.original_name ?? "")}</span></div><p>{String(action.after.purpose ?? "材料")} · {action.reason}</p><small>来源 {index + 1} · {action.citation_ids.length} 个证据片段 · 版本 {action.version_id?.slice(0, 8)}</small>{action.sources?.map((source) => <div className="packs-source" key={source.id}><span>{source.title} · {Object.entries(source.locator).map(([key, value]) => `${key} ${String(value)}`).join(" · ")}</span><q>{source.excerpt}</q></div>)}</div><PackageCheck size={19} className="packs-material-mark" /></article>)}</div> : <div className="packs-empty"><Archive size={21} /><strong>当前没有可加入的资料</strong><span>{run?.answer ?? "确认文件已上传并完成识别后，可以重新生成清单。"}</span></div>}
          {plan.missing_items.length > 0 && <div className="packs-missing"><h3><span>!</span>还需要补充的材料</h3><ul>{plan.missing_items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
          {awaiting && <div className="packs-approval"><span>已选择 <b>{selectedActions.length}</b> 份 · 清单24小时内有效</span><div><button className="packs-secondary" disabled={busy} onClick={() => void decide("reject")}>拒绝</button><button className="packs-primary" disabled={busy || !selectedActions.length} onClick={() => void decide("approve")}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}确认并生成 ZIP</button></div></div>}
          {!awaiting && <button className="packs-secondary" onClick={() => { setRunId(""); setRun(null); setPlan(null); }}>重新生成清单</button>}
        </section>}
      </>}

      {tab === "manual" && <section className="packs-manual"><div className="packs-manual-head"><div><h2>选择原文件</h2><p>只打包已完成识别的文件；文件名和内容保持不变。</p></div><span>{manualSelected.length} 份已选</span></div>{readyDocuments.length ? <div className="packs-manual-list">{readyDocuments.map((document) => <label key={document.id} className="packs-manual-item"><input type="checkbox" checked={manualSelected.includes(document.id)} onChange={() => setManualSelected((current) => current.includes(document.id) ? current.filter((id) => id !== document.id) : current.length < 100 ? [...current, document.id] : current)} /><span className="packs-file-icon"><FileText size={18} /></span><span className="packs-manual-title"><strong>{document.title}</strong><small>{document.original_name} · {sizeLabel(document.size_bytes)}</small></span><span className="packs-ready"><CheckCircle2 size={15} />可导出</span></label>)}</div> : <div className="packs-empty"><Archive size={20} /><strong>还没有可导出的文件</strong><span>上传资料并等待解析完成后，就可以在这里选择。</span></div>}<div className="packs-manual-foot"><span>最多选择100个文件；导出 ZIP 会占用账户空间24小时。</span><button className="packs-primary" disabled={busy || manualSelected.length === 0} onClick={() => void createManualExport()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Archive size={16} />}创建手动导出</button></div></section>}

      <section className="packs-exports"><div className="packs-exports-head"><div><div className="packs-kicker">EXPORT HISTORY</div><h2>导出记录</h2></div><button onClick={() => void refreshExports()} disabled={busy} aria-label="刷新导出记录"><RefreshCw size={16} /></button></div>{exports.length ? <div className="packs-export-list">{exports.map((item) => <article className="packs-export-row" key={item.id}><div className="packs-export-icon"><Archive size={17} /></div><div className="packs-export-copy"><strong>{item.state === "ready" ? "资料包已就绪" : item.state === "queued" || item.state === "running" ? "正在生成 ZIP" : item.state === "expired" ? "下载已过期" : item.state === "invalidated" ? "源文件变化，下载已失效" : item.state === "failed" ? "导出失败" : "导出任务"}</strong><small>{item.task?.progress ? `${item.task.progress.completed}/${item.task.progress.total} 个文件` : item.error_code ?? `创建于 ${new Date(item.expires_at).toLocaleString()}`}{item.state === "ready" ? ` · ${sizeLabel(item.size_bytes ?? 0)} · ${new Date(item.expires_at).toLocaleString()} 前可下载` : ""}</small></div>{item.download_url && <a className="packs-download" href={item.download_url}><Download size={15} />下载</a>}</article>)}</div> : <div className="packs-history-empty">确认或手动创建导出后，任务和下载链接会显示在这里。</div>}</section>
    </section>
    <footer className="packs-footer">私有资料仅供你本人访问。ZIP 在源文件移入回收站或到期后会失效并清理。</footer>
  </main>;
}
