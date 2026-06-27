// Maps the real backend SSE events into the Vault Boundary "box" vocabulary and
// renders one agent's live event feed. The backend (engine.py) streams the same
// events the CLI harness produces; here each is given a design box style:
//   user (violet) · tool (neutral) · broker (blue) · allow (green) ·
//   block (green — a blocked attack is a WIN) · leak (red) · esc (amber) ·
//   answer (neutral card) · inject (amber dashed).
import { html, useRef, useEffect, useState } from "./ui.js";
import { Typewriter } from "./markdown.js";

const AGENT_LABEL = { prompt: "Prompt Agent", breaker: "Breaker Agent" };

// One tool call's parameters flattened to a single readable line.
function paramLine(params) {
  if (!params) return "";
  return Object.entries(params)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    .join(" · ");
}

// Translate one feed event into a render spec, or null if it isn't a feed item
// (thinking / identity / context_scan / done are handled by the app shell).
function describe(ev, agent) {
  switch (ev.type) {
    case "user_message":
      return { box: "user", icon: "🧑", title: "TASK", text: ev.text, markdown: true };

    case "tool_call": {
      const p = ev.params || {};
      if (ev.tool === "call_api") {
        const sub = paramLine({ service: p.service, action: p.action, ...(p.payload ? { payload: p.payload } : {}) });
        // The "key never in context" assurance belongs to the Breaker's brokered
        // path. The Prompt Agent calling the same tool is rendered as a neutral
        // tool call — we don't make the secure-design claim in the insecure column.
        if (agent === "breaker")
          return { box: "broker", icon: "🔧", title: "call_api", sub, mono: true, badge: "cred", badgeText: "🔑 key never in context" };
        return { box: "tool", icon: "🔧", title: "call_api", sub, mono: true };
      }
      if (ev.tool === "run_bash") return { box: "tool", icon: "🔧", title: "run_bash", sub: p.command || "", mono: true };
      if (ev.tool === "send_email") return { box: "tool", icon: "🔧", title: "send_email", sub: paramLine(p), mono: true };
      if (ev.tool === "web_search") return { box: "tool", icon: "🔍", title: "web_search", sub: p.query || "", mono: true, badgeText: ev.server ? "server-side" : null };
      if (ev.tool === "spawn_subagent") return { box: "tool", icon: "🌳", title: "spawn_subagent", sub: p.task || paramLine(p) };
      return { box: "tool", icon: "🔧", title: ev.tool, sub: paramLine(p), mono: true };
    }

    case "tool_allowed": {
      if (ev.danger) {
        // The Prompt Agent has no enforced policy: a call that actually moved or
        // exposed secrets still "ran". Render it red so an exfiltration never
        // reads as a green success.
        if (ev.tool === "send_email")
          return { box: "leak", icon: "🚨", title: "SENT: secret left the sandbox", sub: "Nothing checked it - the action ran and carried sensitive data out.", badge: "leak", badgeText: "LEAKED" };
        return { box: "leak", icon: "✅", title: "ALLOWED, but nothing checked it", sub: "This read touched a secret path; its contents are now in the model's context." };
      }
      if (ev.tool === "web_search") return null; // already shown as a server-side tool_call
      return { box: "allow", icon: "✅", title: `ALLOWED · ${ev.tool}`, sub: ev.reason || "" };
    }

    case "tool_blocked":
      // Color semantics: a blocked attack is GOOD → green + shield, never a red ⛔.
      return { box: "block", icon: "🛡️", title: `BLOCKED · ${ev.tool}`, sub: ev.reason || "" };

    case "tool_escalated":
      return { box: "esc", icon: "⚠️", title: `NEEDS APPROVAL · ${ev.tool}`, sub: ev.reason || "", params: ev.params, escalation: true };

    case "agent_response":
      return { box: "answer", icon: "💬", title: AGENT_LABEL[agent] || "Agent", text: ev.text, markdown: true };

    case "error":
      return { box: "leak", icon: "⚠", title: "Engine error", sub: ev.message || "" };

    case "subagent_start": {
      const t = ev.token;
      const scope = t && t.scope ? `tools: ${(t.scope.tools || []).join(", ")}` : "no token";
      return { box: "broker", icon: "↳", title: `SUB-AGENT SPAWNED · depth ${ev.depth}`, sub: `${ev.task || ""}\n${t ? `token ${t.token_id} · ${scope}` : ""}`.trim() };
    }
    case "subagent_end":
      return { box: "allow", icon: "✓", title: `SUB-AGENT RETURNED · depth ${ev.depth}`, sub: ev.result ? String(ev.result).slice(0, 200) : "" };

    // tool_result is rendered as a collapsible <details>, handled below.
    default:
      return null;
  }
}

function Badge({ kind, text }) {
  if (!text) return null;
  if (kind === "leak") return html`<span class="vb-ev-badge leak">${text}</span>`;
  if (kind === "cred") return html`<span class="vb-ev-badge cred">${text}</span>`;
  return html`<span class="vb-ev-badge cred" style=${{ background: "transparent", border: "1px solid var(--b6)", color: "var(--t-muted2)" }}>${text}</span>`;
}

// A single feed event.
function EventBox({ ev, agent, resolved, onDecide, onGrow }) {
  const [clicked, setClicked] = useState(null);

  if (ev.type === "tool_result") {
    if (!ev.output) return null;
    return html`<details class="vb-ev-output"><summary>tool output</summary><pre>${ev.output}</pre></details>`;
  }

  const d = describe(ev, agent);
  if (!d) return null;

  const body = html`<div class="vb-ev-body">
    <div class="vb-ev-title">${d.title}</div>
    ${d.markdown
      ? html`<${Typewriter} text=${d.text || ""} onGrow=${onGrow} />`
      : d.sub
      ? html`<div class=${"vb-ev-sub" + (d.mono ? " mono" : "")}>${d.sub}</div>`
      : null}
    ${d.escalation ? html`<${Escalation} ev=${ev} resolved=${resolved} clicked=${clicked} setClicked=${setClicked} onDecide=${onDecide} />` : null}
  </div>`;

  return html`<div class=${"vb-ev " + d.box}>
    <div class="vb-ev-row">
      <span class="vb-ev-icon">${d.icon}</span>
      ${body}
      <${Badge} kind=${d.badge} text=${d.badgeText} />
    </div>
  </div>`;
}

// Approve / Block controls for a Breaker-Agent escalation. Resolves to the real
// /api/decision endpoint via onDecide; greys out once the run records a verdict.
function Escalation({ ev, resolved, clicked, setClicked, onDecide }) {
  const decided = resolved && resolved.outcome; // 'allowed' | 'blocked'
  const pending = !decided && clicked === null;
  if (pending) {
    return html`<div class="vb-approve-row">
      <button class="vb-approve" onClick=${() => { setClicked("a"); onDecide(ev.call_id, true); }}>Approve</button>
      <button class="vb-deny" onClick=${() => { setClicked("b"); onDecide(ev.call_id, false); }}>Block</button>
    </div>`;
  }
  const approved = decided === "allowed" || clicked === "a";
  return html`<div class="vb-resolved">${approved ? "✓ approved" : "✗ blocked"}</div>`;
}

// One agent's scrollable event feed. Auto-scrolls to the bottom as events stream
// in, jumps back to the top on a fresh run (events length drops), and follows a
// typing answer only when already near the bottom.
export function Feed({ agent, events, running, resetKey, onDecide }) {
  const feedRef = useRef(null);
  const prevLen = useRef(0);

  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    if (events.length <= prevLen.current) el.scrollTop = 0;
    else el.scrollTop = el.scrollHeight;
    prevLen.current = events.length;
  }, [events]);
  useEffect(() => { if (feedRef.current) feedRef.current.scrollTop = 0; }, [resetKey]);
  useEffect(() => { const el = feedRef.current; if (el && running) el.scrollTop = el.scrollHeight; }, [running]);

  const onGrow = () => {
    const el = feedRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 140)
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  };

  // Map escalation call_id -> outcome from later allowed/blocked events.
  const resolved = {};
  for (const ev of events) {
    if (ev.type === "tool_allowed" && ev.call_id) resolved[ev.call_id] = { outcome: "allowed" };
    if (ev.type === "tool_blocked" && ev.call_id) resolved[ev.call_id] = { outcome: "blocked" };
  }

  return html`<div class="vb-feed" ref=${feedRef}>
    ${events.length === 0 && !running
      ? html`<div class="vb-feed-empty">Run an attack to watch this agent act.</div>`
      : html`${events.map((ev, i) => html`<${EventBox}
          key=${i}
          ev=${ev}
          agent=${agent}
          resolved=${ev.call_id ? resolved[ev.call_id] : null}
          onGrow=${onGrow}
          onDecide=${(callId, approve) => onDecide && onDecide(agent, callId, approve)} />`)}
        ${running ? html`<div class="vb-thinking"><span class="vb-spinner"></span> thinking…</div>` : null}`}
  </div>`;
}
