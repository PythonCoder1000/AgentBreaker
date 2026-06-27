// The live event feed for one agent: a column of feed items (tool calls,
// verdicts, escalations, agent/user bubbles) that auto-scrolls as it grows.
import { html, useState, useRef, useEffect } from "./ui.js";
import { Typewriter } from "./markdown.js";

// One tool call's parameters.
function Params({ params }) {
  if (!params) return null;
  const rows = Object.entries(params).map(([k, v]) => {
    const val = Array.isArray(v) ? v.join(", ") : String(v);
    return html`<div class="row" key=${k}><span class="k">${k}</span>  ${val}</div>`;
  });
  return html`<div class="params">${rows}</div>`;
}

// The per-agent context inspector strip: live proof of whether any credential is
// present in the model's context. Clean (green) for the brokered Breaker path;
// "exposed" (red) when a secret was read into context (the Prompt Agent path).
function ContextInspector({ scan }) {
  if (!scan) return null;
  if (scan.clean) {
    return html`<div class="ctx-inspector clean">
      <span class="ci-icon">🔒</span>
      <span class="ci-text">Context inspector: <b>clean</b> — no credential is in the model's context</span>
    </div>`;
  }
  const items = (scan.findings || []).map((f) => `${f.label} (${f.preview})`).join(", ");
  const plural = scan.count === 1 ? "" : "s";
  return html`<div class="ctx-inspector dirty">
    <span class="ci-icon">🚨</span>
    <span class="ci-text">Context inspector: <b>${scan.count} secret${plural} exposed</b> in the model's context${items ? " — " + items : ""}</span>
  </div>`;
}

// A single feed event.
function FeedItem({ ev, resolved, onDecide, onGrow }) {
  const [clicked, setClicked] = useState(null);

  switch (ev.type) {
    case "user_message":
      return html`<div class="item bubble user">
        <span class="who">Injected prompt</span>
        <${Typewriter} text=${ev.text} onGrow=${onGrow} />
      </div>`;

    case "tool_call": {
      const brokered = ev.tool === "call_api";
      const caption = brokered
        ? "The agent reached an external service through the access layer:"
        : "The agent tried to:";
      return html`<div class=${"item tool-call" + (brokered ? " brokered" : "")}>
        <div class="tc-caption">${caption}</div>
        <div class="tc-head"><span class="glyph">⏺</span><span class="tname">${ev.tool}</span>
          ${ev.server ? html`<span style=${{ color: "var(--dim)", fontSize: "11px" }}>server-side</span>` : null}
          ${brokered ? html`<span class="brokered-tag">🔑 credential never in context</span>` : null}
        </div>
        <${Params} params=${ev.params} />
      </div>`;
    }

    case "tool_allowed":
      // The Prompt Agent has no enforced policy, so a call that actually moved or
      // exposed secrets still "runs" — flag it red (not green) so an exfiltration
      // doesn't read as a success. The word ALLOWED stays (the tool did allow it).
      if (ev.danger) {
        const tag = ev.tool === "send_email" ? "🚨 LEAKED" : "⚠ SECRETS EXPOSED";
        return html`<div class="item verdict allowed danger">
          <span class="vg">🚨</span>
          <div class="vbody">
            <span class="vtitle">ALLOWED · ${ev.tool} <span class="danger-tag">${tag}</span></span>
            <span class="vreason">No policy stopped this — the action ran and moved sensitive data.</span>
          </div>
        </div>`;
      }
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
        <${Typewriter} text=${ev.text} onGrow=${onGrow} />
      </div>`;

    case "error":
      return html`<div class="item err">⚠ ${ev.message}</div>`;

    case "tool_result":
      if (!ev.output) return null;
      return html`<details class="item output">
        <summary>output</summary>
        <pre>${ev.output}</pre>
      </details>`;

    case "subagent_start": {
      const t = ev.token;
      const scopeStr = t ? `tools: ${(t.scope && t.scope.tools || []).join(", ")}` : "no token";
      const depthStr = `depth ${ev.depth}`;
      return html`<div class="item subagent-start">
        <span class="vg">↳</span>
        <div class="vbody">
          <span class="vtitle">SUB-AGENT SPAWNED · ${depthStr}</span>
          <span class="vreason">${ev.task}</span>
          ${t ? html`<div class="tc-mini">
            <span class="tc-mini-k">Token</span> <span class="tc-mini-v mono">${t.token_id}</span>
            <span class="tc-mini-sep">·</span>
            <span class="tc-mini-k">Derived from</span> <span class="tc-mini-v mono">${t.parent_token_id || "—"}</span>
            <br />
            <span class="tc-mini-k">Scope</span> <span class="tc-mini-v">${scopeStr}</span>
          </div>` : null}
        </div>
      </div>`;
    }

    case "subagent_end":
      return html`<div class="item subagent-end">
        <span class="vg">✓</span>
        <div class="vbody">
          <span class="vtitle">SUB-AGENT RETURNED · depth ${ev.depth}</span>
          ${ev.result ? html`<span class="vreason">${ev.result.slice(0, 200)}</span>` : null}
        </div>
      </div>`;

    default:
      return null;
  }
}

// One agent column: its event feed plus a composer that messages this agent only.
export function Column({ kind, title, events, running, resetKey, scan, onSend, onDecide }) {
  const feedRef = useRef(null);
  const prevLen = useRef(0);
  const [input, setInput] = useState("");
  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    // A new run/session clears the feed (length drops to 0 / shrinks): jump back
    // to the top so the first thing the audience sees is the start of the run.
    // While the run streams (events only ever append), follow along to the bottom.
    if (events.length <= prevLen.current) el.scrollTop = 0;
    else el.scrollTop = el.scrollHeight;
    prevLen.current = events.length;
  }, [events]);
  // Every new run/session bumps resetKey: force BOTH panels' scroll back to the
  // top so the two columns start from the same place (they can otherwise sit at
  // different scroll points from the previous run).
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0;
  }, [resetKey]);
  // Keep the "thinking…" row in view as soon as a run starts.
  useEffect(() => {
    const el = feedRef.current;
    if (el && running) el.scrollTop = el.scrollHeight;
  }, [running]);

  // Follow text as a message types out — but only when already near the bottom,
  // so a viewer who scrolled up to read isn't yanked back down. Without this a
  // long final response types out below the fold and looks cut off mid-sentence.
  const onGrow = () => {
    const el = feedRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 140) {
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    }
  };

  // map escalation call_id -> outcome, from later allowed/blocked events
  const resolved = {};
  for (const ev of events) {
    if (ev.type === "tool_allowed" && ev.call_id) resolved[ev.call_id] = { outcome: "allowed" };
    if (ev.type === "tool_blocked" && ev.call_id) resolved[ev.call_id] = { outcome: "blocked" };
  }

  const send = () => {
    const text = input.trim();
    if (!text || running) return;
    setInput("");
    onSend(text);
  };

  return html`<div class=${"column " + kind}>
    <div class="column-head">
      <span class="tag"></span>
      <h2>${title}</h2>
    </div>
    <${ContextInspector} scan=${scan} />
    <div class="feed" ref=${feedRef}>
      ${events.length === 0 && !running
        ? html`<div class="empty">Press <b>RUN</b> for a preset, or message this agent below.</div>`
        : html`${events.map((ev, i) => html`<${FeedItem}
            key=${i}
            ev=${ev}
            resolved=${ev.call_id ? resolved[ev.call_id] : null}
            onGrow=${onGrow}
            onDecide=${(callId, approve) => onDecide(kind, callId, approve)} />`)}
          ${running ? html`<div class="item thinking"><span class="spinner"></span> thinking…</div>` : null}`}
    </div>
    <div class="col-composer">
      <textarea rows="1"
        placeholder=${"Message the " + title + "…"}
        value=${input}
        disabled=${running}
        onInput=${(ev) => setInput(ev.target.value)}
        onKeyDown=${(ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); } }} />
      <button class="btn run" disabled=${running || !input.trim()} onClick=${send}>Send</button>
    </div>
  </div>`;
}
