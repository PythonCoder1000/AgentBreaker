// App shell: top-level state, the SSE wiring that drives both agent columns, and
// the Home/Chat view switch. Component modules live alongside this file and are
// imported as relative ES modules (no build step — htm + React over a CDN).
import { html, createRoot, useState, useEffect, useRef, useCallback, AGENTS, newId } from "./ui.js";
import { HomeView } from "./home.js";
import { ChatView } from "./chat.js";

function Navbar({ view, setView }) {
  const link = (id, label) =>
    html`<button class=${"nav-link" + (view === id ? " active" : "")} onClick=${() => setView(id)}>${label}</button>`;
  return html`<nav class="navbar">
    <div class="brand"><h1>Agent<span class="dot">Breaker</span></h1></div>
    <div class="nav-links">${link("home", "Home")}${link("chat", "Chat")}</div>
  </nav>`;
}

function App() {
  const [view, setView] = useState("home");
  const [scenarios, setScenarios] = useState([]);
  const [selected, setSelected] = useState(0);
  const [running, setRunning] = useState({ prompt: false, breaker: false });
  const [feeds, setFeeds] = useState({ prompt: [], breaker: [] });
  // Per-agent identity token display objects (null until identity_issued arrives).
  const [identity, setIdentity] = useState({ prompt: null, breaker: null });
  // Per-agent context-inspector report (null until context_scan arrives).
  const [scans, setScans] = useState({ prompt: null, breaker: null });
  // Bumped on every new run/session so both columns reset their scroll to the top.
  const [runSeq, setRunSeq] = useState(0);

  const esRef = useRef({ prompt: null, breaker: null });
  const sessionRef = useRef(null);

  useEffect(() => {
    sessionRef.current = newId();
    fetch("/api/scenarios").then((r) => r.json()).then(setScenarios).catch(() => {});
  }, []);

  const closeStream = useCallback((agent) => {
    const es = esRef.current[agent];
    if (es) { es.close(); esRef.current[agent] = null; }
  }, []);
  const closeStreams = useCallback(() => { AGENTS.forEach(closeStream); }, [closeStream]);

  // Open one SSE for a single agent; setParams(p) adds scenario= or message=.
  // Completion is signalled by clearing this agent's `running` flag (the column's
  // "thinking…" row disappears) — there's no end-of-run marker event.
  const openStream = useCallback((agent, setParams) => {
    closeStream(agent);
    setRunning((r) => ({ ...r, [agent]: true }));
    const p = new URLSearchParams({ session: sessionRef.current, agent });
    setParams(p);
    const es = new EventSource(`/api/stream?${p.toString()}`);
    const finish = () => { es.close(); esRef.current[agent] = null; setRunning((r) => ({ ...r, [agent]: false })); };
    es.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      // `thinking` is driven by the running flag; `done` just ends the stream —
      // neither is rendered as a feed item.
      if (ev.type === "done") { finish(); return; }
      if (ev.type === "thinking") return;
      // Identity events update the trust chain panel, not the event feed.
      if (ev.type === "identity_issued") {
        setIdentity((id) => ({ ...id, [agent]: ev.token }));
        return;
      }
      if (ev.type === "identity_revoked") {
        setIdentity((id) => ({
          ...id,
          [agent]: id[agent] ? { ...id[agent], revoked: true } : null,
        }));
        return;
      }
      // Context-inspector report updates the per-column strip, not the feed.
      if (ev.type === "context_scan") {
        setScans((s) => ({ ...s, [agent]: { clean: ev.clean, count: ev.count, findings: ev.findings } }));
        return;
      }
      setFeeds((f) => ({ ...f, [agent]: [...f[agent], ev] }));
    };
    es.onerror = finish;
    esRef.current[agent] = es;
  }, [closeStream]);

  const newSession = useCallback(() => {
    closeStreams();
    const old = sessionRef.current;
    if (old) {
      fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: old }) }).catch(() => {});
    }
    sessionRef.current = newId();
    setFeeds({ prompt: [], breaker: [] });
    setIdentity({ prompt: null, breaker: null });
    setScans({ prompt: null, breaker: null });
    setRunning({ prompt: false, breaker: false });
    setRunSeq((s) => s + 1);                       // reset both panels' scroll
  }, [closeStreams]);

  // Revoke the capability token for one agent's current session.
  const onRevoke = useCallback((agent) => {
    if (!sessionRef.current) return;
    fetch("/api/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sessionRef.current, agent }),
    }).catch(() => {});
  }, []);

  // Verify the tamper-evident audit chain for the current session. Returns a
  // promise of {ok, length, broken_at, reason} (or null on failure).
  const onVerifyAudit = useCallback(() => {
    const id = sessionRef.current;
    if (!id) return Promise.resolve(null);
    return fetch(`/api/audit/${encodeURIComponent(id)}/verify`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }, []);

  const runScenario = useCallback((idx) => {
    if (!scenarios.length) return;
    newSession();                                  // a preset starts a clean session
    AGENTS.forEach((agent) => openStream(agent, (p) => p.set("scenario", scenarios[idx].id)));
  }, [scenarios, newSession, openStream]);

  // Send a free-text message to ONE agent, continuing the current session.
  const sendToAgent = useCallback((agent, text) => {
    const msg = text.trim();
    if (!msg || running[agent]) return;
    openStream(agent, (p) => p.set("message", msg));
  }, [running, openStream]);

  const onDecide = useCallback((agent, callId, approve) => {
    if (!sessionRef.current) return;
    fetch("/api/decision", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sessionRef.current, agent, call_id: callId, approve }) }).catch(() => {});
  }, []);

  // keyboard (chat view only): N -> next preset + run, R -> replay
  useEffect(() => {
    const onKey = (e) => {
      if (view !== "chat") return;
      if (e.target && /input|select|textarea/i.test(e.target.tagName)) return;
      if (e.key === "n" || e.key === "N") {
        setSelected((s) => { const next = scenarios.length ? (s + 1) % scenarios.length : 0; runScenario(next); return next; });
      } else if (e.key === "r" || e.key === "R") {
        runScenario(selected);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, scenarios, selected, runScenario]);

  return html`<div class="app">
    <${Navbar} view=${view} setView=${setView} />
    ${view === "home"
      ? html`<${HomeView} scenarios=${scenarios} setView=${setView} />`
      : html`<${ChatView}
          scenarios=${scenarios} selected=${selected} setSelected=${setSelected}
          running=${running} feeds=${feeds} runSeq=${runSeq} identity=${identity} scans=${scans}
          runScenario=${runScenario} sendToAgent=${sendToAgent} newSession=${newSession}
          onDecide=${onDecide} onRevoke=${onRevoke} onVerifyAudit=${onVerifyAudit} />`}
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
