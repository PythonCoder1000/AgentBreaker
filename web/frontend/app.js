import React from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);
const e = React.createElement;
const { useState, useEffect, useRef, useCallback } = React;

const AGENTS = ["prompt", "breaker"];

// ---- minimal markdown (builds React nodes directly — no innerHTML/XSS) ------
// Ordered so `code` and links win before emphasis. Asterisk emphasis only (what
// Claude emits); avoids lookbehind so it parses on every browser.
const INLINE_PATTERNS = [
  { re: /`([^`]+)`/, build: (m, k) => e("code", { key: k }, m[1]) },
  {
    re: /\[([^\]]+)\]\(([^)\s]+)\)/,
    build: (m, k) => {
      const href = /^(https?:|mailto:)/i.test(m[2]) ? m[2] : "#";
      return e("a", { key: k, href, target: "_blank", rel: "noopener noreferrer" }, inline(m[1]));
    },
  },
  { re: /\*\*\*([^]+?)\*\*\*/, build: (m, k) => e("strong", { key: k }, e("em", null, inline(m[1]))) },
  { re: /\*\*([^]+?)\*\*/, build: (m, k) => e("strong", { key: k }, inline(m[1])) },
  { re: /\*([^*\n]+?)\*/, build: (m, k) => e("em", { key: k }, inline(m[1])) },
];

function inline(text) {
  const out = [];
  let buf = text;
  let key = 0;
  while (buf) {
    let best = null;
    for (const p of INLINE_PATTERNS) {
      const m = p.re.exec(buf);
      if (m && (best === null || m.index < best.m.index)) best = { p, m };
    }
    if (!best) { out.push(buf); break; }
    if (best.m.index > 0) out.push(buf.slice(0, best.m.index));
    out.push(best.p.build(best.m, "x" + key++));
    buf = buf.slice(best.m.index + best.m[0].length);
  }
  return out;
}

function renderMarkdown(text) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {                       // fenced code block
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
      i++; // skip closing fence
      blocks.push(e("pre", { key: key++, className: "md-pre" }, e("code", null, code.join("\n"))));
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);          // heading
    if (h) { blocks.push(e("h" + Math.min(h[1].length + 2, 6), { key: key++ }, inline(h[2]))); i++; continue; }
    if (!line.trim()) { i++; continue; }               // blank → paragraph break
    if (/^\s*[-*+]\s+/.test(line)) {                   // unordered list
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++; }
      blocks.push(e("ul", { key: key++ }, items.map((it, idx) => e("li", { key: idx }, inline(it)))));
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {                   // ordered list
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
      blocks.push(e("ol", { key: key++ }, items.map((it, idx) => e("li", { key: idx }, inline(it)))));
      continue;
    }
    const para = [];                                   // paragraph (joined with <br>)
    while (i < lines.length && lines[i].trim() &&
           !/^\s*(```|[-*+]\s+|\d+\.\s+|#{1,6}\s+)/.test(lines[i])) { para.push(lines[i]); i++; }
    const kids = [];
    para.forEach((p, idx) => { if (idx) kids.push(e("br", { key: "br" + idx })); inline(p).forEach((n) => kids.push(n)); });
    blocks.push(e("p", { key: key++ }, kids));
  }
  return blocks;
}

// ---- typewriter (reveals text, rendering markdown as it goes) ---------------
function Typewriter({ text, speed = 10 }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    if (!text) return;
    let i = 0;
    const id = setInterval(() => {
      i += Math.max(1, Math.round(text.length / 400)); // finish long text in ~4s
      setN(Math.min(i, text.length));
      if (i >= text.length) clearInterval(id);
    }, speed);
    return () => clearInterval(id);
  }, [text]);
  return e("div", { className: "md" }, renderMarkdown(text.slice(0, n)));
}

// ---- one tool call's parameters -------------------------------------------
function Params({ params }) {
  if (!params) return null;
  const rows = Object.entries(params).map(([k, v]) => {
    const val = Array.isArray(v) ? v.join(", ") : String(v);
    return html`<div class="row" key=${k}><span class="k">${k}</span>  ${val}</div>`;
  });
  return html`<div class="params">${rows}</div>`;
}

// ---- a single feed event ---------------------------------------------------
function FeedItem({ ev, isLast, resolved, onDecide }) {
  const [clicked, setClicked] = useState(null);

  switch (ev.type) {
    case "user_message":
      return html`<div class="item bubble user">
        <span class="who">Injected prompt</span>
        <${Typewriter} text=${ev.text} />
      </div>`;

    case "thinking":
      // a thinking row only matters while it's the latest event
      if (!isLast) return null;
      return html`<div class="item thinking"><span class="spinner"></span> thinking…</div>`;

    case "tool_call":
      return html`<div class="item tool-call">
        <div class="tc-head"><span class="glyph">⏺</span><span class="tname">${ev.tool}</span>
          ${ev.server ? html`<span style=${{ color: "var(--dim)", fontSize: "11px" }}>server-side</span>` : null}
        </div>
        <${Params} params=${ev.params} />
      </div>`;

    case "tool_allowed":
      return html`<div class="item verdict allowed">
        <span class="vg">✓</span>
        <div class="vbody"><span class="vtitle">ALLOWED · ${ev.tool}</span>
          ${ev.reason ? html`<span class="vreason">${ev.reason}</span>` : null}
        </div>
      </div>`;

    case "tool_blocked":
      return html`<div class="item verdict blocked">
        <span class="vg">🚨</span>
        <div class="vbody"><span class="vtitle">BLOCKED · ${ev.tool}</span>
          <span class="vreason">${ev.reason}</span>
        </div>
      </div>`;

    case "tool_escalated": {
      const decided = resolved?.outcome; // 'allowed' | 'blocked' once resolved
      const pending = !decided && clicked === null;
      return html`<div class="item verdict escalated">
        <span class="vg">⚠️</span>
        <div class="vbody" style=${{ flex: 1 }}>
          <span class="vtitle">ESCALATION · ${ev.tool}</span>
          <span class="vreason">${ev.reason}</span>
          <${Params} params=${ev.params} />
          ${pending
            ? html`<div class="approve-row">
                <button class="approve" onClick=${() => { setClicked("a"); onDecide(ev.call_id, true); }}>[ A ] Approve</button>
                <button class="deny" onClick=${() => { setClicked("b"); onDecide(ev.call_id, false); }}>[ B ] Block</button>
              </div>`
            : html`<div class="resolved-note">${
                decided === "allowed" || clicked === "a" ? "✓ approved" : "✗ blocked"
              }</div>`}
        </div>
      </div>`;
    }

    case "agent_response":
      return html`<div class="item bubble agent">
        <span class="who">Agent</span>
        <${Typewriter} text=${ev.text} />
      </div>`;

    case "error":
      return html`<div class="item err">⚠ ${ev.message}</div>`;

    case "done":
      return html`<div class="item done">— run complete —</div>`;

    case "tool_result":
      if (!ev.output) return null;
      return html`<details class="item output">
        <summary>output</summary>
        <pre>${ev.output}</pre>
      </details>`;

    default:
      return null;
  }
}

// ---- one agent column ------------------------------------------------------
function Column({ kind, title, events, onDecide }) {
  const feedRef = useRef(null);
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  // map escalation call_id -> outcome, from later allowed/blocked events
  const resolved = {};
  for (const ev of events) {
    if (ev.type === "tool_allowed" && ev.call_id) resolved[ev.call_id] = { outcome: "allowed" };
    if (ev.type === "tool_blocked" && ev.call_id) resolved[ev.call_id] = { outcome: "blocked" };
  }

  return html`<div class=${"column " + kind}>
    <div class="column-head">
      <span class="tag"></span>
      <h2>${title}</h2>
    </div>
    <div class="feed" ref=${feedRef}>
      ${events.length === 0
        ? html`<div class="empty">Press <b>RUN</b> for a preset, or send a message below.</div>`
        : events.map((ev, i) => html`<${FeedItem}
            key=${i}
            ev=${ev}
            isLast=${i === events.length - 1}
            resolved=${ev.call_id ? resolved[ev.call_id] : null}
            onDecide=${(callId, approve) => onDecide(kind, callId, approve)} />`)}
    </div>
  </div>`;
}

// ---- app -------------------------------------------------------------------
function Navbar({ view, setView }) {
  const link = (id, label) =>
    html`<button class=${"nav-link" + (view === id ? " active" : "")} onClick=${() => setView(id)}>${label}</button>`;
  return html`<nav class="navbar">
    <div class="brand"><h1>Agent<span class="dot">Breaker</span></h1></div>
    <div class="nav-links">${link("home", "Home")}${link("chat", "Chat")}</div>
  </nav>`;
}

// ---- home: comparison table ------------------------------------------------
const STATUS = {
  works: { icon: "✓", label: "Holds", cls: "ok" },
  fails: { icon: "✗", label: "Fails", cls: "bad" },
  uncertain: { icon: "~", label: "Uncertain", cls: "warn" },
};
function StatusCell({ status }) {
  const s = STATUS[status] || STATUS.uncertain;
  return html`<span class=${"status " + s.cls}><b>${s.icon}</b> ${s.label}</span>`;
}
function HomeView({ scenarios, setView }) {
  return html`<div class="home">
    <div class="home-hero">
      <h2>Two agents, one job — which one actually holds the line?</h2>
      <p>Both run the same tasks with the same tools. The <b class="c-prompt">Prompt Agent</b>
        relies on guardrails written into its system prompt. The
        <b class="c-breaker">Breaker Agent</b> drops those prompt rules and instead routes
        every tool call through a dedicated security framework that inspects what each call
        actually does.</p>
    </div>
    <table class="compare">
      <thead><tr>
        <th>Attack</th>
        <th><span class="c-prompt">Prompt Agent</span><span class="th-sub">system-prompt rules</span></th>
        <th><span class="c-breaker">Breaker Agent</span><span class="th-sub">dedicated security framework</span></th>
      </tr></thead>
      <tbody>
        ${scenarios.map((s) => html`<tr key=${s.id}>
          <td class="atk"><b>${s.name}</b><span class="atk-sub">${s.tagline}</span></td>
          <td><${StatusCell} status=${s.prompt_status} /></td>
          <td><${StatusCell} status=${s.breaker_status} /></td>
        </tr>`)}
      </tbody>
    </table>
    <button class="btn run" onClick=${() => setView("chat")}>Try it live →</button>
  </div>`;
}

// ---- chat view -------------------------------------------------------------
function ChatView({ scenarios, selected, setSelected, running, feeds, input, setInput,
                    runScenario, sendMessage, newSession, onDecide }) {
  const current = scenarios[selected];
  return html`<div class="chat">
    <div class="controls-bar">
      <div class="select-wrap">
        <label>Preset attack</label>
        <select value=${selected} onChange=${(e) => setSelected(Number(e.target.value))}>
          ${scenarios.map((s, i) => html`<option key=${s.id} value=${i}>${s.name}</option>`)}
        </select>
      </div>
      <button class="btn run" disabled=${running || !scenarios.length} onClick=${() => runScenario(selected)}>
        ${running ? "RUNNING…" : "▶ RUN"}
      </button>
      <button class="btn ghost" disabled=${running} onClick=${newSession}>+ New session</button>
      <div class="kbd"><span><b>N</b> next</span><span><b>R</b> replay</span></div>
    </div>

    ${current ? html`<div class="scenario-bar"><b>Preset:</b> ${current.task}</div>` : null}

    <div class="columns">
      <${Column} kind="prompt" title="Prompt Agent" events=${feeds.prompt} onDecide=${onDecide} />
      <${Column} kind="breaker" title="Breaker Agent" events=${feeds.breaker} onDecide=${onDecide} />
    </div>

    <div class="composer">
      <textarea rows="1"
        placeholder="Message both agents…  (Enter to send · Shift+Enter for a newline)"
        value=${input}
        onInput=${(ev) => setInput(ev.target.value)}
        onKeyDown=${(ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendMessage(); } }} />
      <button class="btn run" disabled=${running || !input.trim()} onClick=${sendMessage}>Send</button>
    </div>
  </div>`;
}

// ---- app -------------------------------------------------------------------
function newId() {
  return (crypto.randomUUID && crypto.randomUUID()) || String(Math.random());
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
