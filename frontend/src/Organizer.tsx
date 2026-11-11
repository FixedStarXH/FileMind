import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ArrowLeft, Check, CheckCircle2, Clock3, FolderOpen, LoaderCircle, ShieldCheck, Sparkles, X } from "lucide-react";
import { request } from "./api";
import "./Organizer.css";

type Folder = { id: string; name: string; parent_id: string | null };
type Tag = { id: string; name: string; color: string };
type Action = {
  id: string; type: "rename" | "move" | "set_tags"; document_id: string; version_id: string | null;
  expected_revision: number; before: Record<string, unknown>; after: Record<string, unknown>;
  reason: string; citation_ids: string[];
};
type Plan = { id: string; run_id: string; plan_hash: string; status: string; expires_at: string; actions: Action[] };
type Run = { id: string; status: string; answer: string | null; error: string | null; plan_id: string | null };

const DEFAULT_INSTRUCTION = "为这些资料建议清晰的分类、文件名和标签。保留原意，只提出有帮助的调整。";
const errors: Record<string, string> = {
  AI_NOT_CONFIGURED: "管理员尚未配置 DeepSeek API Key。",
  AI_DISABLED: "请先在账户设置中启用 AI。",
  FINITE_SCOPE_REQUIRED: "请选择收件箱、文件夹或标签范围。",
  SCOPE_TOO_LARGE: "所选范围超过 100 份资料，请缩小范围。",
  STALE_PLAN: "资料在方案生成后发生了变化，请重新生成方案。",
  PLAN_EXPIRED: "方案已过期，请重新生成。",
  ALREADY_DECIDED: "此方案已经处理。",
  CONVERSATION_BUSY: "当前整理任务仍在进行中。",
};

function errorMessage(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  return errors[code] ?? "暂时无法完成操作，请稍后重试。";
}

function describeAction(action: Action, folders: Map<string, string>, tags: Map<string, string>) {
  if (action.type === "rename") return { label: "重命名", before: String(action.before.title ?? ""), after: String(action.after.title ?? "") };
  if (action.type === "move") {
    const before = String(action.before.folder_name ?? "收件箱");
    const id = action.after.folder_id ? String(action.after.folder_id) : "";
    return { label: "移动位置", before, after: id ? folders.get(id) ?? String(action.after.folder_name ?? "文件夹") : "收件箱" };
  }
  const before = (Array.isArray(action.before.tag_names) ? action.before.tag_names : []).map(String).join("、") || "无标签";
  const after = (Array.isArray(action.after.tag_ids) ? action.after.tag_ids : []).map((id) => tags.get(String(id)) ?? "标签").join("、") || "无标签";
  return { label: "调整标签", before, after };
}

export default function Organizer({
  csrf, hasDeepSeek, aiEnabled, onLibrary, onSettings, onLogout,
}: {
  csrf: string; hasDeepSeek: boolean; aiEnabled: boolean; onLibrary: () => void; onSettings: () => void; onLogout: () => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [scope, setScope] = useState("inbox");
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const folderNames = useMemo(() => new Map(folders.map((item) => [item.id, item.name])), [folders]);
  const tagNames = useMemo(() => new Map(tags.map((item) => [item.id, item.name])), [tags]);

  useEffect(() => {
    void Promise.all([
      request<{ items: Folder[] }>("/folders"),
      request<{ items: Tag[] }>("/tags"),
    ]).then(([folderResult, tagResult]) => {
      setFolders(folderResult.data.items);
      setTags(tagResult.data.items);
    }).catch((cause) => setError(errorMessage(cause)));
  }, []);

  useEffect(() => {
    if (!runId || !["queued", "running"].includes(run?.status ?? "")) return;
    let live = true;
    let source: EventSource | null = null;
    const loadRun = async () => {
      try {
        const result = await request<Run>(`/agent/runs/${runId}`);
        if (!live) return;
        setRun(result.data);
        if (result.data.plan_id) {
          const planResult = await request<Plan>(`/agent/runs/${runId}/plan`);
          if (!live) return;
          setPlan(planResult.data);
          setSelected(planResult.data.actions.map((item) => item.id));
        }
      } catch { /* the stream or next poll will report the final state */ }
    };
    source = new EventSource(`/api/v1/agent/runs/${runId}/events`, { withCredentials: true });
    const onApproval = () => { void loadRun(); source?.close(); };
    const onDone = () => { void loadRun(); source?.close(); };
    source.addEventListener("approval.required", onApproval);
    source.addEventListener("run.completed", onDone);
    source.addEventListener("run.failed", onDone);
    const timer = window.setInterval(() => void loadRun(), 1800);
    void loadRun();
    return () => {
      live = false;
      window.clearInterval(timer);
      source?.close();
    };
  }, [runId, run?.status]);

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(""); setNotice(""); setPlan(null); setRun(null); setSelected([]); setBusy(true);
    const selectedScope = scope === "inbox"
      ? { document_ids: [], folder_ids: [], tag_ids: [], inbox: true, all_documents: false }
      : scope.startsWith("folder:")
        ? { document_ids: [], folder_ids: [scope.slice(7)], tag_ids: [], inbox: false, all_documents: false }
        : { document_ids: [], folder_ids: [], tag_ids: [scope.slice(4)], inbox: false, all_documents: false };
    try {
      const result = await request<{ run_id: string }>("/agent/runs", {
        method: "POST", headers: { "X-CSRF-Token": csrf },
        body: JSON.stringify({ mode: "organize", instruction, scope: selectedScope }),
      });
      setRunId(result.data.run_id);
      setRun({ id: result.data.run_id, status: "queued", answer: null, error: null, plan_id: null });
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function decide(decision: "approve" | "reject") {
    if (!plan || !runId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await request<{ status: string }>(`/agent/runs/${runId}/approval`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          plan_id: plan.id, plan_hash: plan.plan_hash, decision,
          selected_action_ids: decision === "approve" ? selected : [],
        }),
      });
      setRun((current) => current ? { ...current, status: result.data.status } : current);
      setPlan((current) => current ? { ...current, status: decision === "approve" ? "approved" : "rejected" } : current);
      setNotice(decision === "approve" ? "已确认所选建议，正在应用。" : "已拒绝这份方案。资料没有变更。" );
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  function toggleAction(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  const processing = run?.status === "queued" || run?.status === "running";
  const awaiting = run?.status === "awaiting_approval" && plan?.status === "awaiting_approval";

  return (
    <main className="organizer-shell">
      <header className="organizer-topbar">
        <button className="organizer-back" onClick={onLibrary}><ArrowLeft size={17} />返回资料库</button>
        <div className="organizer-brand"><span><Sparkles size={16} /></span><strong>资料整理</strong><small>AI 建议 · 你来确认</small></div>
        <div className="organizer-account"><span>{hasDeepSeek ? "模型已配置" : "模型未配置"}</span><button onClick={onSettings}>设置</button><button onClick={onLogout}>退出</button></div>
      </header>
      <section className="organizer-body">
        <div className="organizer-intro">
          <div className="organizer-kicker">PERSONAL LIBRARY · ORGANIZE</div>
          <h1>让资料归位，<em>由你决定。</em></h1>
          <p>选择一组资料，知档会提出文件名、位置或标签建议。任何更改都要你逐项确认后才会应用。</p>
          <div className="organizer-steps"><span><i>01</i>选择范围</span><b /><span><i>02</i>预览差异</span><b /><span><i>03</i>确认应用</span></div>
        </div>

        {!hasDeepSeek && <div className="organizer-warning"><ShieldCheck size={16} /><span>AI 尚未配置：请部署管理员在服务端配置 DeepSeek API Key。你仍可在资料库手动编辑文件名、文件夹和标签。</span><button type="button" onClick={onLibrary}>去资料库手动整理</button></div>}
        {hasDeepSeek && !aiEnabled && <div className="organizer-warning"><ShieldCheck size={16} />请先在账户设置中启用 AI，再生成整理方案。<button type="button" onClick={onSettings}>打开设置</button></div>}
        {error && <div className="organizer-error" role="alert">{error}<button onClick={() => setError("")} aria-label="关闭"><X size={15} /></button></div>}
        {notice && <div className="organizer-notice" role="status"><CheckCircle2 size={15} />{notice}</div>}

        {!runId && <form className="organizer-form" onSubmit={(event) => void createPlan(event)}>
          <label className="organizer-field"><span>整理范围</span><div className="organizer-select"><FolderOpen size={16} /><select value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="inbox">收件箱</option>
            {folders.map((folder) => <option key={folder.id} value={`folder:${folder.id}`}>文件夹 · {folder.name}</option>)}
            {tags.map((tag) => <option key={tag.id} value={`tag:${tag.id}`}>标签 · {tag.name}</option>)}
          </select></div></label>
          <label className="organizer-field"><span>整理要求</span><textarea value={instruction} maxLength={4000} onChange={(event) => setInstruction(event.target.value)} /></label>
          <div className="organizer-form-foot"><span><ShieldCheck size={14} />仅提出元数据建议；不会读取其他账户资料，也不会自动修改文件。</span><button className="organizer-primary" disabled={busy || !hasDeepSeek || !aiEnabled || !instruction.trim()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}生成整理方案</button></div>
        </form>}

        {processing && <div className="organizer-progress"><LoaderCircle className="spin" size={19} /><div><strong>正在检查选定资料</strong><span>方案生成后会在这里显示，资料不会自动修改。</span></div><Clock3 size={16} /></div>}

        {plan && <section className="organizer-plan" aria-labelledby="plan-title">
          <div className="organizer-plan-head"><div><div className="organizer-kicker">PROPOSAL · {plan.actions.length} ITEMS</div><h2 id="plan-title">整理方案</h2><p>逐项检查修改前后的内容；取消勾选的建议不会应用。</p></div><span className={`organizer-plan-status status-${plan.status}`}>{plan.status === "awaiting_approval" ? "等待确认" : plan.status === "approved" ? "正在应用" : plan.status === "completed" ? "已完成" : plan.status === "rejected" ? "已拒绝" : plan.status}</span></div>
          {plan.actions.length === 0 ? <div className="organizer-empty"><CheckCircle2 size={19} /><strong>暂时没有需要调整的资料</strong><span>{run?.answer ?? "所选范围内没有生成可应用的修改建议。"}</span></div> : <div className="organizer-action-list">
            {plan.actions.map((action, index) => {
              const description = describeAction(action, folderNames, tagNames);
              const canSelect = awaiting;
              return <article className={`organizer-action ${selected.includes(action.id) ? "is-selected" : ""}`} key={action.id}>
                <div className="organizer-action-top"><span className="organizer-action-index">{String(index + 1).padStart(2, "0")}</span><div className="organizer-action-title"><strong>{String(action.before.title ?? action.after.title ?? "资料")}</strong><span>{description.label}</span></div><button type="button" className={`organizer-check ${selected.includes(action.id) ? "checked" : ""}`} disabled={!canSelect} onClick={() => toggleAction(action.id)} aria-label={`${selected.includes(action.id) ? "取消选择" : "选择"}第${index + 1}项`}>{selected.includes(action.id) && <Check size={14} />}</button></div>
                <div className="organizer-diff"><div><small>当前</small><span>{description.before}</span></div><i>→</i><div><small>建议</small><span>{description.after}</span></div></div>
                <p className="organizer-reason">{action.reason}</p>
              </article>;
            })}
          </div>}
          {awaiting && <div className="organizer-approval-bar"><span>已选择 <b>{selected.length}</b> 项 · 方案 24 小时内有效</span><div><button className="organizer-reject" disabled={busy} onClick={() => void decide("reject")}>拒绝方案</button><button className="organizer-primary" disabled={busy || selected.length === 0} onClick={() => void decide("approve")}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}确认应用 {selected.length} 项</button></div></div>}
          {!awaiting && plan.status === "completed" && <button className="organizer-secondary" onClick={() => { setRunId(""); setRun(null); setPlan(null); }}>重新整理</button>}
        </section>}
      </section>
      <footer className="organizer-footer">知档只会在确认后修改文件名、所在文件夹或标签。<span>原始文件内容不会被改写。</span></footer>
    </main>
  );
}
