// App shell for the Vault Boundary single-page demo. Holds the top-level demo
// state plus the SSE wiring that drives both agent columns, and composes the
// static marketing sections (sections.js) around the live demo (demo.js). No build
// step — htm + React over a CDN.
import { html, createRoot, useState, useEffect, useRef, useCallback, AGENTS, newId } from "./ui.js";
import { Nav, Hero, Beats, HowItWorks, Compare, Transparency, Footer } from "./sections.js";
import { LiveDemo } from "./demo.js";

function App() {
  const [scenarios, setScenarios] = useState([]);
  const [selected, setSelected] = useState(0);
  const [running, setRunning] = useState({ prompt: false, breaker: false });
  const [feeds, setFeeds] = useState({ prompt: [], breaker: [] });
  // Per-agent identity token (null until identity_issued arrives).
  const [identity, setIdentity] = useState({ prompt: null, breaker: null });
  // Per-agent context-inspector report (null until context_scan arrives).
  const [scans, setScans] = useState({ prompt: null, breaker: null });
  // Bumped on every new run so both columns reset their scroll to the top.
  const [runSeq, setRunSeq] = useState(0);

  const esRef = useRef({ prompt: null, breaker: null });
  const sessionRef = useRef(null);

  useEffect(() => {
    sessionRef.current = newId();
    // Default to Attack 1 — the most reliable contrast: the Prompt Agent attaches a
    // file that secretly holds a key and emails it out, so it leaks consistently,
    // while the Breaker blocks the send. (Attack 4 depends on the model taking the
    // injection bait, so both agents can end up holding the line.)
    fetch("/api/scenarios").then((r) => r.json()).then((list) => {
      setScenarios(list);
      const i = list.findIndex((s) => s.id === "attack-1");
      if (i >= 0) setSelected(i);
    }).catch(() => {});
  }, []);

  const closeStream = useCallback((agent) => {
    const es = esRef.current[agent];
    if (es) { es.close(); esRef.current[agent] = null; }
  }, []);
  const closeStreams = useCallback(() => { AGENTS.forEach(closeStream); }, [closeStream]);

  // Open one SSE for a single agent; setParams(p) adds scenario= or message=.
  // Completion clears this agent's `running` flag (its "thinking…" row disappears).
  const openStream = useCallback((agent, setParams) => {
    closeStream(agent);
    setRunning((r) => ({ ...r, [agent]: true }));
    const p = new URLSearchParams({ session: sessionRef.current, agent });
    setParams(p);
    const es = new EventSource(`/api/stream?${p.toString()}`);
    let gotEvent = false; // did the stream ever connect/produce anything?
    const finish = () => { es.close(); esRef.current[agent] = null; setRunning((r) => ({ ...r, [agent]: false })); };
    es.onmessage = (e) => {
      gotEvent = true;
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      if (ev.type === "done") { finish(); return; }
      if (ev.type === "thinking") return;
      if (ev.type === "identity_issued") { setIdentity((id) => ({ ...id, [agent]: ev.token })); return; }
      if (ev.type === "identity_revoked") {
        setIdentity((id) => ({ ...id, [agent]: id[agent] ? { ...id[agent], revoked: true } : null }));
        return;
      }
      if (ev.type === "context_scan") {
        setScans((s) => ({ ...s, [agent]: { clean: ev.clean, count: ev.count, findings: ev.findings } }));
        return;
      }
      setFeeds((f) => ({ ...f, [agent]: [...f[agent], ev] }));
    };
    // A connection that never produced an event (bad API key, revoked session,
    // unknown scenario, server down) would otherwise just clear the column with no
    // explanation — surface a single error row so the failure is visible.
    es.onerror = () => {
      if (!gotEvent) {
        setFeeds((f) => ({ ...f, [agent]: [...f[agent], {
          type: "error",
          message: "Could not start the agent stream. Check that the server is running and ANTHROPIC_API_KEY is set (see the server logs).",
        }] }));
      }
      finish();
    };
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
    setRunSeq((s) => s + 1);
  }, [closeStreams]);

  // "Run both": the same task streams into BOTH columns at once.
  //  - A typed prompt is a follow-up: it CONTINUES the current chat (the backend
  //    keeps per-session history and each message resumes from it), so a multi-turn
  //    conversation accumulates until the user starts a new chat.
  //  - A preset attack starts a FRESH chat (new session).
  const runDemo = useCallback((idx, customText) => {
    if (!scenarios.length) return;
    const text = (customText || "").trim();
    if (text) {
      AGENTS.forEach((agent) => openStream(agent, (p) => p.set("message", text)));
    } else {
      newSession();
      AGENTS.forEach((agent) => openStream(agent, (p) => p.set("scenario", scenarios[idx].id)));
    }
  }, [scenarios, newSession, openStream]);

  const onDecide = useCallback((agent, callId, approve) => {
    if (!sessionRef.current) return;
    fetch("/api/decision", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sessionRef.current, agent, call_id: callId, approve }) }).catch(() => {});
  }, []);

  const onRevoke = useCallback((agent) => {
    if (!sessionRef.current) return;
    fetch("/api/revoke", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sessionRef.current, agent }) }).catch(() => {});
  }, []);

  // Verify the tamper-evident audit chain for the current session. Resolves to
  // {ok, length, broken_at, reason} (or null on failure).
  const onVerifyAudit = useCallback(() => {
    const id = sessionRef.current;
    if (!id) return Promise.resolve(null);
    return fetch(`/api/audit/${encodeURIComponent(id)}/verify`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }, []);

  return html`<div class="vb-page">
    <${Nav} />
    <${Hero} />
    <${Beats} />
    <${LiveDemo}
      scenarios=${scenarios} selected=${selected} setSelected=${setSelected}
      running=${running} feeds=${feeds} scans=${scans} identity=${identity} runSeq=${runSeq}
      onRun=${runDemo} onNewChat=${newSession} onDecide=${onDecide} onRevoke=${onRevoke} onVerifyAudit=${onVerifyAudit} />
    <${HowItWorks} />
    <${Compare} />
    <${Transparency} />
    <${Footer} />
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
