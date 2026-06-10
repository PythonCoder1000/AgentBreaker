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
    setRunning({ prompt: false, breaker: false });
  }, [closeStreams]);

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
          running=${running} feeds=${feeds}
          runScenario=${runScenario} sendToAgent=${sendToAgent} newSession=${newSession}
          onDecide=${onDecide} />`}
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
