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

// A single feed event.
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

// One agent column.
export function Column({ kind, title, events, onDecide }) {
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
