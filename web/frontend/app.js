import React from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(React.createElement);
const { useState, useEffect, useRef, useCallback } = React;

const AGENTS = ["prompt", "breaker"];

// ---- typewriter ------------------------------------------------------------
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
  return html`<span>${text.slice(0, n)}</span>`;
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
function Column({ kind, title, expect, events, onDecide }) {
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
      <span class="expect">${expect}</span>
    </div>
    <div class="feed" ref=${feedRef}>
      ${events.length === 0
        ? html`<div class="empty">Pick a scenario and press <b>RUN</b>.</div>`
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
function App() {
  const [scenarios, setScenarios] = useState([]);
  const [selected, setSelected] = useState(0);
  const [running, setRunning] = useState(false);
  const [feeds, setFeeds] = useState({ prompt: [], breaker: [] });

  const esRef = useRef([]);
  const sessionRef = useRef(null);
  const doneRef = useRef(0);

  useEffect(() => {
    fetch("/api/scenarios").then((r) => r.json()).then(setScenarios).catch(() => {});
  }, []);

  const closeStreams = useCallback(() => {
    esRef.current.forEach((es) => es.close());
    esRef.current = [];
  }, []);

  const run = useCallback((idx) => {
    if (!scenarios.length) return;
    const scenario = scenarios[idx];
    closeStreams();
    const session = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random());
    sessionRef.current = session;
    doneRef.current = 0;
    setFeeds({ prompt: [], breaker: [] });
    setRunning(true);

    esRef.current = AGENTS.map((agent) => {
      const url = `/api/stream?session=${session}&agent=${agent}&scenario=${scenario.id}`;
      const es = new EventSource(url);
      es.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        setFeeds((f) => ({ ...f, [agent]: [...f[agent], ev] }));
        if (ev.type === "done") {
          es.close();
          doneRef.current += 1;
          if (doneRef.current >= AGENTS.length) setRunning(false);
        }
      };
      es.onerror = () => {
        es.close();
        doneRef.current += 1;
        if (doneRef.current >= AGENTS.length) setRunning(false);
      };
      return es;
    });
  }, [scenarios, closeStreams]);

  const onDecide = useCallback((agent, callId, approve) => {
    if (!sessionRef.current) return;
    fetch("/api/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: sessionRef.current, agent, call_id: callId, approve }),
    }).catch(() => {});
  }, []);

  // keyboard: N -> next scenario + run, R -> replay current
  useEffect(() => {
    const onKey = (e) => {
      if (e.target && /input|select|textarea/i.test(e.target.tagName)) return;
      if (e.key === "n" || e.key === "N") {
        setSelected((s) => {
          const next = scenarios.length ? (s + 1) % scenarios.length : 0;
          run(next);
          return next;
        });
      } else if (e.key === "r" || e.key === "R") {
        run(selected);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [scenarios, selected, run]);

  const current = scenarios[selected];

  return html`<div class="app">
    <div class="header">
      <div class="brand">
        <h1>Agent<span class="dot">Breaker</span></h1>
        <span class="sub">Prompt Agent vs Breaker Agent · live policy demo</span>
      </div>
      <div class="controls">
        <div class="select-wrap">
          <label>Scenario</label>
          <select value=${selected} onChange=${(e) => setSelected(Number(e.target.value))}>
            ${scenarios.map((s, i) => html`<option key=${s.id} value=${i}>${s.name}</option>`)}
          </select>
        </div>
        <button class="btn run" disabled=${running || !scenarios.length} onClick=${() => run(selected)}>
          ${running ? "RUNNING…" : "▶ RUN BOTH"}
        </button>
        <div class="kbd"><span><b>N</b> next</span><span><b>R</b> replay</span></div>
      </div>
    </div>

    ${current
      ? html`<div class="scenario-bar"><b>Task:</b> ${current.task}</div>`
      : null}

    <div class="columns">
      <${Column} kind="prompt" title="Prompt Agent"
        expect=${current ? current.expected_prompt : ""}
        events=${feeds.prompt} onDecide=${onDecide} />
      <${Column} kind="breaker" title="Breaker Agent"
        expect=${current ? current.expected_breaker : ""}
        events=${feeds.breaker} onDecide=${onDecide} />
    </div>
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
