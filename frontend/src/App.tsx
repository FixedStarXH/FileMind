import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Check, Eye, EyeOff, FileText, HardDrive, KeyRound, LogOut, ShieldCheck } from "lucide-react";
import Library from "./Library";
import Assistant from "./Assistant";
import Organizer from "./Organizer";
import TaskCenter from "./TaskCenter";
import ApplicationPacks from "./ApplicationPacks";
import { request } from "./api";
import "./auth.css";

type User = { id: string; email: string };
type AccountSettings = {
  ai_enabled: boolean;
  quota_bytes: number;
  used_bytes: number;
  reserved_bytes: number;
  capabilities: { deepseek: boolean; keyword_search: boolean; vector_search: boolean };
};
type CitationTarget = { document_id: string; version_id: string; title?: string; locator: Record<string, unknown> };
type AppView = "library" | "tasks" | "settings" | "assistant" | "organizer" | "packs";
const validViews = new Set<AppView>(["library", "tasks", "settings", "assistant", "organizer", "packs"]);
function viewFromPath(pathname: string): AppView {
  const segment = pathname.split("/").filter(Boolean)[0] ?? "library";
  return validViews.has(segment as AppView) ? segment as AppView : "library";
}
const errorText: Record<string, string> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  INVALID_OLD_PASSWORD: "当前密码不正确。",
  VALIDATION_ERROR: "请检查邮箱格式和密码长度。",
  INVALID_CREDENTIALS: "邮箱或密码不正确。",
  RATE_LIMITED: "尝试次数过多，请稍后再试。",
  LOGIN_SERVICE_UNAVAILABLE: "登录服务暂不可用，请稍后重试。",
  CSRF_INVALID: "请求校验失败，请刷新页面后重试。",
  AI_NOT_CONFIGURED: "管理员还没有配置 DeepSeek API Key。",
  EMAIL_ALREADY_REGISTERED: "该邮箱已注册，请直接登录。",
  REGISTRATION_CLOSED: "当前暂停新用户注册，请联系管理员。",
  REGISTRATION_UNAVAILABLE: "注册服务暂不可用，请稍后重试。",
};

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [settings, setSettings] = useState<AccountSettings | null>(null);
  const [csrf, setCsrf] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [registerConfirm, setRegisterConfirm] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setViewState] = useState<AppView>(() => viewFromPath(window.location.pathname));
  const [citationTarget, setCitationTarget] = useState<CitationTarget | null>(null);

  function navigate(nextView: AppView, replace = false) {
    setViewState(nextView);
    const path = nextView === "library" ? "/" : `/${nextView}`;
    if (window.location.pathname !== path) {
      window.history[replace ? "replaceState" : "pushState"]({ view: nextView }, "", path);
    }
  }

  async function loadAccount() {
    const me = await request<{ user: User; csrf_token: string }>("/auth/me");
    const account = await request<AccountSettings>("/settings");
    setUser(me.data.user);
    setCsrf(me.data.csrf_token);
    setSettings(account.data);
  }

  useEffect(() => {
    void loadAccount().catch(() => undefined);
    const onPopState = () => setViewState(viewFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await request<{ user: User; csrf_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setUser(result.data.user);
      navigate("library", true);
      setCsrf(result.data.csrf_token);
      setPassword("");
      const account = await request<AccountSettings>("/settings");
      setSettings(account.data);
    } catch (cause) {
      setError(errorText[(cause as Error).message] ?? "暂时无法登录，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (password.length < 12) { setError("注册密码至少需要 12 个字符。"); return; }
    if (password !== registerConfirm) { setError("两次输入的密码不一致。"); return; }
    setBusy(true);
    try {
      const result = await request<{ user: User; csrf_token: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setUser(result.data.user);
      navigate("library", true);
      setCsrf(result.data.csrf_token);
      setPassword("");
      setRegisterConfirm("");
      const account = await request<AccountSettings>("/settings");
      setSettings(account.data);
    } catch (cause) {
      setError(errorText[(cause as Error).message] ?? "暂时无法注册，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  async function toggleAi() {
    if (!settings) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await request<AccountSettings>("/settings", {
        method: "PATCH",
        headers: { "X-CSRF-Token": csrf },
        body: JSON.stringify({ ai_enabled: !settings.ai_enabled }),
      });
      setSettings(updated.data);
      setNotice(updated.data.ai_enabled ? "AI 已启用。所选资料片段会发送到 DeepSeek。" : "AI 已关闭。\n");
    } catch (cause) {
      setError(errorText[(cause as Error).message] ?? "设置暂时无法保存。");
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    try {
      await request<void>("/auth/password", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
      });
      setUser(null);
      setSettings(null);
      setCsrf("");
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setNotice("密码已更新，请使用新密码重新登录。");
    } catch (cause) {
      setError(errorText[(cause as Error).message] ?? "密码暂时无法更新。");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      await request<void>("/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
    } catch (cause) {
      setError(errorText[(cause as Error).message] ?? "退出登录失败，请重试。");
    } finally {
      setUser(null);
      setSettings(null);
      setCsrf("");
      setBusy(false);
    }
  }

  if (user && settings && view === "library") {
    return (
      <Library
        user={user}
        settings={settings}
        csrf={csrf}
        busy={busy}
        setBusy={setBusy}
        onSettings={() => navigate("settings")}
        onTasks={() => navigate("tasks")}
        onAssistant={() => navigate("assistant")}
        onOrganizer={() => navigate("organizer")}
        onApplicationPacks={() => navigate("packs")}
        citationTarget={citationTarget}
        onLogout={() => void logout()}
      />
    );
  }

  if (user && settings && view === "tasks") {
    return <TaskCenter csrf={csrf} onLibrary={() => navigate("library")} onSettings={() => navigate("settings")} onLogout={() => void logout()} />;
  }

  if (user && settings && view === "assistant") {
    return <Assistant userEmail={user.email} settings={settings} csrf={csrf} onLibrary={() => navigate("library")} onOpenCitation={(citation) => { setCitationTarget(citation); navigate("library"); }} onSettings={() => navigate("settings")} onLogout={() => void logout()} />;
  }

  if (user && settings && view === "organizer") {
    return <Organizer csrf={csrf} hasDeepSeek={settings.capabilities.deepseek} aiEnabled={settings.ai_enabled} onLibrary={() => navigate("library")} onSettings={() => navigate("settings")} onLogout={() => void logout()} />;
  }

  if (user && settings && view === "packs") {
    return <ApplicationPacks csrf={csrf} aiEnabled={settings.ai_enabled} hasDeepSeek={settings.capabilities.deepseek} onLibrary={() => navigate("library")} onSettings={() => navigate("settings")} onLogout={() => void logout()} />;
  }

  if (user && settings) {
    const used = settings.used_bytes + settings.reserved_bytes;
    const percent = settings.quota_bytes ? Math.min(100, (used / settings.quota_bytes) * 100) : 0;
    return (
      <main className="account-shell">
        <header className="account-topbar">
          <a className="brand" href="#home"><span className="brand-mark"><FileText size={18} /></span>知档</a>
          <button className="quiet-button" onClick={() => navigate("library")}><FileText size={16} /><span>返回资料库</span></button>
          <span className="account-email">{user.email}</span>
          <button className="quiet-button" onClick={() => void logout()} disabled={busy} aria-label="退出登录"><LogOut size={17} /><span>退出</span></button>
        </header>
        <section className="account-content">
          <div className="eyebrow">账户空间</div>
          <h1>资料库准备好了。</h1>
          <p className="lead">这里会集中管理你的课件、论文、证书和求职材料。</p>
          <div className="account-grid">
            <article className="setting-card storage-card">
              <div className="card-icon"><HardDrive size={19} /></div>
              <div className="card-label">存储空间</div>
              <div className="storage-number">{formatBytes(used)} <span>/ {formatBytes(settings.quota_bytes)}</span></div>
              <div className="meter" role="progressbar" aria-label="存储空间使用量" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
              <p className="helper">已用空间包含正在处理的上传。</p>
            </article>
            <article className="setting-card ai-card">
              <div className="card-icon"><ShieldCheck size={19} /></div>
              <div className="card-label">AI 资料问答</div>
              <div className="toggle-row">
                <div><strong>{settings.ai_enabled ? "已启用" : "已关闭"}</strong><p className="helper">启用后，所选资料片段会发送到 DeepSeek。</p></div>
                <button className={`switch ${settings.ai_enabled ? "is-on" : ""}`} onClick={() => void toggleAi()} disabled={busy || !settings.capabilities.deepseek} role="switch" aria-checked={settings.ai_enabled} aria-label="启用 AI 资料问答"><span /></button>
              </div>
              {!settings.capabilities.deepseek && <p className="inline-note">管理员尚未配置 DeepSeek API Key</p>}
            </article>
            <article className="setting-card security-card">
              <div className="card-icon"><KeyRound size={19} /></div>
              <div className="card-label">账户安全</div>
              <form className="password-change" onSubmit={(event) => void changePassword(event)}>
                <label htmlFor="old-password">当前密码</label>
                <input id="old-password" type="password" autoComplete="current-password" required maxLength={256} value={oldPassword} onChange={(event) => setOldPassword(event.target.value)} />
                <label htmlFor="new-password">新密码（至少12位）</label>
                <input id="new-password" type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
                <label htmlFor="confirm-password">再次输入新密码</label>
                <input id="confirm-password" type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
                <button className="secondary-button" type="submit" disabled={busy}>更新密码</button>
              </form>
            </article>
          </div>
          {error && <p className="feedback error" role="alert">{error}</p>}
          {notice && <p className="feedback success" role="status">{notice}</p>}
          <div className="next-step"><span className="step-dot"><Check size={14} /></span><span>账户与隐私空间已就绪</span><ArrowRight size={16} className="step-arrow" /></div>
          <p className="footnote">返回资料库即可上传文件，或先在这里查看存储空间与账户安全设置。</p>
        </section>
      </main>
    );
  }

  return (
    <main className="login-shell">
      <aside className="login-aside">
        <a className="brand" href="#home"><span className="brand-mark"><FileText size={18} /></span>知档</a>
        <div className="aside-copy">
          <div className="eyebrow">给重要资料一个好位置</div>
          <h1>你的学习与求职资料，<em>有序收好。</em></h1>
          <p>课件、论文、证书与简历，放进一个只属于你的资料库。</p>
        </div>
        <div className="paper-stack" aria-hidden="true">
          <div className="paper paper-back"><i /><i /><i /></div>
          <div className="paper paper-front"><div className="paper-top"><span>资料档案</span><span>FM—01</span></div><div className="paper-title" /><div className="paper-line wide" /><div className="paper-line" /><div className="paper-line short" /><div className="paper-tag">个人空间 · 私密保存</div></div>
          <span className="paper-index">STUDY / CAREER</span>
        </div>
        <div className="aside-footer"><span>FILEMIND · 个人资料工作台</span><span>01 — 03</span></div>
      </aside>
      <section className="login-panel">
        <div className="login-form-wrap">
          <div className="eyebrow">{authMode === "login" ? "欢迎回来" : "欢迎加入"}</div>
          <h2>{authMode === "login" ? "登录你的资料库" : "创建你的资料库"}</h2>
          <p className="form-intro">{authMode === "login" ? "输入邮箱和密码，继续管理你的资料。" : "注册后即可拥有独立的私人资料空间。"}</p>
          <div className="auth-mode-switch" aria-label="账户入口">
            <button type="button" className={authMode === "login" ? "is-active" : ""} onClick={() => { setAuthMode("login"); setError(""); setPassword(""); setRegisterConfirm(""); }}>登录</button>
            <button type="button" className={authMode === "register" ? "is-active" : ""} onClick={() => { setAuthMode("register"); setError(""); setPassword(""); setRegisterConfirm(""); }}>注册新账户</button>
          </div>
          <form onSubmit={authMode === "login" ? submitLogin : submitRegister} noValidate>
            <label htmlFor="email">邮箱</label>
            <input id="email" type="email" autoComplete="username" required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" />
            <div className="password-label"><label htmlFor="password">{authMode === "register" ? "设置密码（至少 12 位）" : "密码"}</label></div>
            <div className="password-field"><input id="password" type={showPassword ? "text" : "password"} autoComplete={authMode === "register" ? "new-password" : "current-password"} required minLength={authMode === "register" ? 12 : 1} maxLength={256} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={authMode === "register" ? "至少 12 个字符" : "输入密码"} /><button type="button" className="reveal-button" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? "隐藏密码" : "显示密码"}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>
            {authMode === "register" && <><div className="password-label"><label htmlFor="register-confirm">再次输入密码</label></div><input id="register-confirm" type="password" autoComplete="new-password" required maxLength={256} value={registerConfirm} onChange={(event) => setRegisterConfirm(event.target.value)} placeholder="确认密码" /></>}
            {error && <p className="feedback error" role="alert">{error}</p>}
            <button className="submit-button" type="submit" disabled={busy || !email || !password || (authMode === "register" && !registerConfirm)}>{busy ? "正在处理…" : authMode === "register" ? "创建账户并进入资料库" : "登录资料库"}<ArrowRight size={17} /></button>
          </form>
          <div className="login-note"><ShieldCheck size={16} /><span>每个账户都有独立的私人资料空间。</span></div>
          {notice && <p className="feedback success" role="status">{notice}</p>}
        </div>
        <div className="panel-footer"><span>FILEMIND</span><span>安全登录 · 私人资料空间</span></div>
      </section>
    </main>
  );
}
