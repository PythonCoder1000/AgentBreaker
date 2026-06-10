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
  const [running, setRunning] = useState(false);
  const [feeds, setFeeds] = useState({ prompt: [], breaker: [] });
  const [input, setInput] = useState("");

  const esRef = useRef([]);
  const sessionRef = useRef(null);
  const doneRef = useRef(0);

  useEffect(() => {
    sessionRef.current = newId();
    fetch("/api/scenarios").then((r) => r.json()).then(setScenarios).catch(() => {});
  }, []);

  const closeStreams = useCallback(() => {
    esRef.current.forEach((es) => es.close());
    esRef.current = [];
  }, []);

  // Open one SSE per agent; setParams(p, agent) adds scenario= or message=.
  const openStreams = useCallback((setParams) => {
    closeStreams();
    doneRef.current = 0;
    setRunning(true);
    esRef.current = AGENTS.map((agent) => {
      const p = new URLSearchParams({ session: sessionRef.current, agent });
      setParams(p, agent);
      const es = new EventSource(`/api/stream?${p.toString()}`);
      es.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        setFeeds((f) => ({ ...f, [agent]: [...f[agent], ev] }));
        if (ev.type === "done") { es.close(); if (++doneRef.current >= AGENTS.length) setRunning(false); }
      };
      es.onerror = () => { es.close(); if (++doneRef.current >= AGENTS.length) setRunning(false); };
      return es;
    });
  }, [closeStreams]);

  const newSession = useCallback(() => {
    closeStreams();
    const old = sessionRef.current;
    if (old) {
      fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: old }) }).catch(() => {});
    }
    sessionRef.current = newId();
    setFeeds({ prompt: [], breaker: [] });
    setRunning(false);
  }, [closeStreams]);

  const runScenario = useCallback((idx) => {
    if (!scenarios.length) return;
    newSession();                                  // a preset starts a clean session
    openStreams((p) => p.set("scenario", scenarios[idx].id));
  }, [scenarios, newSession, openStreams]);

  const sendMessage = useCallback(() => {
    const text = input.trim();
    if (!text || running) return;
    setInput("");
    openStreams((p) => p.set("message", text));    // continue the current session
  }, [input, running, openStreams]);

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
          running=${running} feeds=${feeds} input=${input} setInput=${setInput}
          runScenario=${runScenario} sendMessage=${sendMessage} newSession=${newSession}
          onDecide=${onDecide} />`}
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
