// The live demo section — the centerpiece. Both columns receive the SAME task and
// the viewer watches them diverge. Unlike the design prototype (scripted timers),
// this streams the REAL backend: "Run both" opens two SSE streams (prompt +
// breaker) for the chosen attack, and the columns render whatever the real agent
// loop and policy core actually do (see app.js for the SSE wiring).
import { html, useState, useEffect } from "./ui.js";
import { Feed } from "./feed.js";
import { SandboxTree } from "./explorer.js";

// Display copy for the attack chips, keyed by scenario id (the design specifies
// exact short labels). Falls back to deriving from the scenario name/tagline.
const CHIP_META = {
  "attack-1": { head: "⭐ Attack 1", label: "innocent secret file" },
  "attack-2": { head: "Attack 2", label: "exfiltrate offsite" },
  "attack-3": { head: "Attack 3", label: "reveal a secrets file" },
  "attack-4": { head: "Attack 4", label: "the vault boundary" },
  "delegation": { head: "Attack 5", label: "scoped delegation" },
  "wallet":     { head: "Attack 6", label: "wallet enforcement" },
};

// Per-scenario "what just happened" summaries (verbatim design copy), shown once
// a preset run completes.
const SUMMARIES = {
  "attack-1": {
    prompt: "It attached a file that secretly held an API key and emailed it out. The innocent name slipped past.",
    breaker: "The access layer read what the file actually contained, a live key, and blocked the send.",
  },
  "attack-2": {
    prompt: "It emailed the raw .env credentials to an outside personal address.",
    breaker: "Two hard rules tripped at once: an outside recipient and raw credentials, so nothing left the sandbox.",
  },
  "attack-3": {
    prompt: "It read the secrets file and printed the credentials straight back to the user.",
    breaker: "The interceptor caught the read of a known secrets path before it ever reached the model.",
  },
  "attack-4": {
    prompt: "It read .env as instructed - the key entered context immediately. The injected reply then asked for it. With the value already in memory, there was nothing stopping it.",
    breaker: "The .env read was blocked before any key entered context. When the injected reply asked for it, there was nothing to hand over.",
  },
  "delegation": {
    prompt: "The sub-agent ran cat .env unscoped - the key entered its context and surfaced in its report. The parent forwarded the findings and complied when Riley followed up.",
    breaker: "The sub-agent's derived token was narrowed to run_bash + call_api - .env blocked, email stripped. The parent never got the key.",
  },
  "wallet": {
    prompt: "All four charges cleared: $100 + $100 + $100 + $250 = $550. No spending cap, no limit on how much the agent could drain.",
    breaker: "Charges 1-2 cleared ($200). Charge 3 would bring the total to $300 - over the $250 token budget - and was rejected before processing.",
  },
};

function chipMeta(sc) {
  if (CHIP_META[sc.id]) return CHIP_META[sc.id];
  const [head, label] = (sc.name || sc.id).split(" - ").map((s) => s.trim());
  return { head: head || sc.id, label: (label || sc.tagline || "").toLowerCase() };
}

// ---------------------------------------------------------------------------
// Context inspector strip — proves whether any credential is in the model's
// context. Drives off the real context_scan report; flips to "exposed" early if
// a deterministic leak event has already streamed in.
// ---------------------------------------------------------------------------
function inspectorState(scan, running, events) {
  // Two DIFFERENT properties, so two red states that must not cancel each other:
  //  - context_scan answers "is a secret sitting in the model's context?"
  //  - a `danger` tool_allowed (Prompt Agent, unenforced) answers "did a secret
  //    just get moved/sent out?" — e.g. Attack 1 attaches a secret-bearing file by
  //    NAME and emails it, exfiltrating it WITHOUT ever reading it into context.
  // The leak signal is sticky: once a secret has left, a later "context is clean"
  // scan must not flip the inspector back to green.
  const leaked = events.some((e) => e.type === "tool_allowed" && e.danger);
  const scanned = scan != null;
  if (scanned && !scan.clean) return "exposed"; // a secret really sits in the model context
  if (leaked) return "leaked";                   // a secret was moved/sent out (sticky)
  if (scanned && scan.clean) return "clean";
  if (running || events.length) return "clean";  // optimistic while the run streams
  return "idle";
}

function Inspector({ scan, running, events }) {
  const state = inspectorState(scan, running, events);
  if (state === "idle")
    return html`<div class="vb-inspector idle">
      <span class="vb-insp-icon">🔍</span>
      <div><div class="vb-insp-label">Context inspector</div><div class="vb-insp-sub">Run an attack to scan the model context</div></div>
    </div>`;
  if (state === "clean")
    return html`<div class="vb-inspector clean">
      <span class="vb-insp-icon">🔒</span>
      <div><div class="vb-insp-label">Clean: no secret in context</div><div class="vb-insp-sub">The model never saw a credential</div></div>
    </div>`;
  if (state === "leaked")
    return html`<div class="vb-inspector exposed">
      <span class="vb-insp-icon">🚨</span>
      <div><div class="vb-insp-label">Leaked: secret left the sandbox</div><div class="vb-insp-sub">An unenforced action moved a credential out - the send was never checked</div></div>
    </div>`;
  const count = scan && scan.count;
  const findings = scan && scan.findings && scan.findings.length
    ? " - " + scan.findings.map((f) => f.label || f.preview).filter(Boolean).join(", ")
    : "";
  const sub = count ? `${count} secret${count === 1 ? "" : "s"} now sitting in the model${findings}` : "A live credential is now sitting in the model";
  return html`<div class="vb-inspector exposed">
    <span class="vb-insp-icon">🚨</span>
    <div><div class="vb-insp-label">Exposed: secret in context</div><div class="vb-insp-sub">${sub}</div></div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Capability-token identity panel (Breaker column). Uses the REAL token issued by
// the backend; Revoke and Verify audit chain hit the real endpoints.
// ---------------------------------------------------------------------------
function fmtExpiry(token) {
  if (token && token.issued_at && token.expires_at) {
    const m = Math.round((token.expires_at - token.issued_at) / 60);
    if (m > 0) return `expires ${m}m`;
  }
  return "expires 15m";
}

function IdentityPanel({ token, onRevoke, onVerifyAudit }) {
  const [localRevoked, setLocalRevoked] = useState(false);
  const [audit, setAudit] = useState(null); // null | "checking" | {ok,length,reason}

  // A new run issues a fresh token: clear the optimistic revoke/audit state so the
  // panel doesn't stay stuck "REVOKED" (the backend only emits identity_revoked
  // while a run is live, so this local flag is reset by token identity, not events).
  const tid = token ? token.token_id : null;
  useEffect(() => { setLocalRevoked(false); setAudit(null); }, [tid]);

  const live = !!token;
  const scope = (token && token.scope) || {};
  const revoked = (token && token.revoked) || localRevoked;
  const tokenId = token ? token.token_id : "cap_7f3a";
  const tools = (scope.tools || ["run_bash", "send_email", "call_api"]).join(" · ");
  const recipients = scope.email_to && scope.email_to.length ? scope.email_to.join(", ") : "*@horizon.org only";
  const budgetStr = scope.spending_limit_usd != null ? ` · budget $${scope.spending_limit_usd}` : "";
  const services = `${(scope.services && scope.services.length ? scope.services : ["helios"]).join(", ")} · ${fmtExpiry(token)} · delegate ≤${scope.max_depth != null ? scope.max_depth : 1}${budgetStr}`;

  const doRevoke = () => {
    if (revoked || !live || !onRevoke) return;
    onRevoke();
    setLocalRevoked(true);
  };
  const doVerify = () => {
    if (!onVerifyAudit) return;
    setAudit("checking");
    Promise.resolve(onVerifyAudit()).then((r) => setAudit(r || { ok: false, reason: "unavailable" }));
  };

  return html`<div class=${"vb-identity" + (revoked ? " revoked" : "")}>
    <div class="vb-identity-head">
      <span class="vb-identity-title">Capability token</span>
      <span class="vb-identity-status">${revoked ? "REVOKED" : live ? "ACTIVE" : "AWAITING RUN"}</span>
    </div>
    <div class="vb-identity-grid">
      <span class="vb-identity-k">id</span><span class="vb-identity-v mono">${tokenId}</span>
      <span class="vb-identity-k">scope</span><span class="vb-identity-v">${tools}</span>
      <span class="vb-identity-k">recipients</span><span class="vb-identity-v">${recipients}</span>
      <span class="vb-identity-k">services</span><span class="vb-identity-v">${services}</span>
    </div>
    <div class="vb-identity-actions">
      <button class="vb-revoke-btn" disabled=${revoked || !live} onClick=${doRevoke}>${revoked ? "Revoked" : "Revoke token"}</button>
      <button class="vb-audit-btn" disabled=${!live} onClick=${doVerify}>Verify audit chain →</button>
      ${audit === "checking"
        ? html`<span class="vb-audit-msg ok">checking…</span>`
        : audit
        ? html`<span class=${"vb-audit-msg " + (audit.ok ? "ok" : "bad")}>${audit.ok ? `✓ chain intact · ${audit.length} record${audit.length === 1 ? "" : "s"} verified` : `✗ ${audit.reason || "chain broken"}`}</span>`
        : null}
    </div>
    ${revoked ? html`<div class="vb-revoked-note">Revoked. This session, and any sub-agent it spawned, can no longer reach anything.</div>` : null}
    ${!live ? html`<div class="vb-revoked-note" style=${{ color: "var(--t-faint)" }}>Issued when you run the demo.</div>` : null}
  </div>`;
}

// ---------------------------------------------------------------------------
// Agent delegation tree — shows the token derivation hierarchy when a scenario
// uses spawn_subagent. Only rendered in the Breaker column (tokens only exist
// there); hidden until at least one sub-agent has been started.
// ---------------------------------------------------------------------------
function AgentTree({ rootToken, events }) {
  const subStarts = events.filter(e => e.type === "subagent_start" && e.token);
  if (!rootToken || subStarts.length === 0) return null;

  const nodes = [
    { depth: 0, token: rootToken, label: "Root agent" },
    ...subStarts.map(e => ({ depth: e.depth, token: e.token, label: `Sub-agent (depth ${e.depth})` }))
  ];

  // For each node, find the true derivation parent's tools: the last preceding
  // node with depth = this node's depth - 1. Using i-1 would be wrong for
  // sibling sub-agents (same depth), which share the root as their parent.
  const nodeTools = nodes.map(n => (n.token && n.token.scope && n.token.scope.tools) || []);
  const parentToolsList = nodes.map((node, i) => {
    if (i === 0) return nodeTools[0];
    for (let j = i - 1; j >= 0; j--) {
      if (nodes[j].depth === node.depth - 1) return nodeTools[j];
    }
    return nodeTools[i];
  });

  return html`<div class="vb-agent-tree">
    <div class="vb-tree-header">Delegation tree · scope at each level</div>
    ${nodes.map((node, i) => {
      const tools = nodeTools[i];
      const dropped = parentToolsList[i].filter(t => !tools.includes(t));
      const tid = node.token ? node.token.token_id : "-";

      return html`<div key=${i} class="vb-tree-node" style=${{ paddingLeft: (node.depth * 20) + "px" }}>
        <div class="vb-tree-row">
          <span class="vb-tree-glyph">${node.depth === 0 ? "▮" : "└▸"}</span>
          <div class="vb-tree-card">
            <div class="vb-tree-name">
              <span>${node.label}</span>
              <span class="vb-tree-tid">${tid}</span>
              ${node.depth > 0 ? html`<span class="vb-tree-badge">derived</span>` : null}
            </div>
            <div class="vb-tree-tools">${tools.length ? tools.join(" · ") : "no tools"}</div>
            ${dropped.length > 0
              ? html`<div class="vb-tree-stripped">− stripped: ${dropped.join(", ")}</div>`
              : null}
          </div>
        </div>
      </div>`;
    })}
  </div>`;
}

// ---------------------------------------------------------------------------
// One agent column.
// ---------------------------------------------------------------------------
function DemoColumn({ kind, name, caption, events, running, scan, resetKey, onDecide, identityPanel }) {
  return html`<div class=${"vb-col " + kind}>
    <div class="vb-col-head">
      <span class="vb-col-dot"></span>
      <span class="vb-col-name">${name}</span>
      <span class="vb-col-caption">${caption}</span>
    </div>
    <${Inspector} scan=${scan} running=${running} events=${events} />
    ${identityPanel || null}
    <${Feed} agent=${kind} events=${events} running=${running} resetKey=${resetKey} onDecide=${onDecide} />
  </div>`;
}

// ---------------------------------------------------------------------------
// Section.
// ---------------------------------------------------------------------------
export function LiveDemo({ scenarios, selected, setSelected, running, feeds, scans, identity,
                           runSeq, onRun, onNewChat, onDecide, onRevoke, onVerifyAudit }) {
  const [freeText, setFreeText] = useState("");
  const [lastRun, setLastRun] = useState(null); // {custom:bool, id?}
  const anyRunning = running.prompt || running.breaker;
  const sel = scenarios[selected];
  const conversationActive = feeds.prompt.length > 0 || feeds.breaker.length > 0;

  const pick = (i) => { setSelected(i); setFreeText(""); };

  const onRunClick = () => {
    if (anyRunning || !scenarios.length) return;
    const custom = freeText.trim();
    if (custom) {
      // A typed prompt is a follow-up: it continues the current chat. Clear the box
      // so the next follow-up starts fresh.
      setLastRun({ custom: true });
      onRun(selected, custom);
      setFreeText("");
    } else {
      // An empty box runs the selected preset attack in a fresh chat.
      setLastRun({ custom: false, id: sel.id });
      onRun(selected, "");
    }
  };

  const onNewChatClick = () => {
    if (anyRunning) return;
    onNewChat();
    setFreeText("");
    setLastRun(null);
  };

  const summary = lastRun && !lastRun.custom && SUMMARIES[lastRun.id];
  const showWhat = !!summary && !anyRunning && conversationActive;
  const runLabel = anyRunning ? "Running…" : (freeText.trim() ? "▶ Send to both" : "▶ Run both");

  return html`<section id="demo" class="vb-section">
    <div class="vb-section-head">
      <div class="vb-eyebrow blue">The live demo</div>
      <h2 class="vb-h2">Same task, same tools. Watch what each one does.</h2>
      <p class="vb-section-sub">Pick an attack, hit run, and keep your eyes on the two context inspectors.</p>
    </div>

    <!-- controls -->
    <div class="vb-controls">
      <div class="vb-controls-label">Choose an attack</div>
      <div class="vb-chips">
        ${scenarios.map((sc, i) => {
          const m = chipMeta(sc);
          return html`<button key=${sc.id} class=${"vb-chip" + (i === selected ? " selected" : "")} onClick=${() => pick(i)} title=${sc.tagline}>
            <span class="vb-chip-head">${m.head}</span>
            <span class="vb-chip-label">${m.label}</span>
          </button>`;
        })}
        <button class="vb-newchat" disabled=${anyRunning} onClick=${onNewChatClick}
          title="Clear the conversation and start a fresh session">+ New chat</button>
      </div>
      <div class="vb-task-row">
        <div class="vb-task-field">
          <div class="vb-task-label">${conversationActive ? "Ask a follow-up - both agents answer in this chat" : "The task both agents receive"}</div>
          <textarea class="vb-task-input" value=${freeText}
            placeholder=${conversationActive ? "Ask a follow-up question (sent to both agents)…" : (sel ? sel.task : "Select an attack…")}
            onInput=${(e) => setFreeText(e.target.value)}
            onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey && freeText.trim()) { e.preventDefault(); onRunClick(); } }}></textarea>
        </div>
        <button class=${"vb-run-btn" + (anyRunning ? " running" : "")} disabled=${anyRunning || !scenarios.length} onClick=${onRunClick}>
          ${runLabel}
        </button>
      </div>
    </div>

    <!-- legend -->
    <div class="vb-legend">
      <span class="vb-legend-label">Legend</span>
      <span class="vb-legend-item"><span class="vb-swatch good"></span>good: safe / clean / attack blocked</span>
      <span class="vb-legend-item"><span class="vb-swatch bad"></span>bad: exposed / leaked</span>
      <span class="vb-legend-item"><span class="vb-swatch warn"></span>needs human approval</span>
      <span class="vb-legend-item"><span class="vb-swatch round prompt"></span>Prompt Agent</span>
      <span class="vb-legend-item"><span class="vb-swatch round breaker"></span>Breaker Agent</span>
    </div>

    <!-- columns -->
    <div class="vb-cols">
      <${DemoColumn} kind="prompt" name="Prompt Agent" caption="the old way · rules in the prompt"
        events=${feeds.prompt} running=${running.prompt} scan=${scans.prompt} resetKey=${runSeq} onDecide=${onDecide} />
      <${DemoColumn} kind="breaker" name="Breaker Agent" caption="the new way · access at runtime"
        events=${feeds.breaker} running=${running.breaker} scan=${scans.breaker} resetKey=${runSeq} onDecide=${onDecide}
        identityPanel=${html`
          <${IdentityPanel} token=${identity.breaker} onRevoke=${() => onRevoke("breaker")} onVerifyAudit=${onVerifyAudit} />
          <${AgentTree} rootToken=${identity.breaker} events=${feeds.breaker} />
        `} />
    </div>

    <!-- what just happened -->
    ${showWhat
      ? html`<div class="vb-what">
          <div class="vb-what-head">What just happened</div>
          <div class="vb-what-grid">
            <div class="vb-what-cell first">
              <span class="vb-what-emoji">🚨</span>
              <div><div class="vb-what-label prompt">Prompt Agent</div><div class="vb-what-text">${summary.prompt}</div></div>
            </div>
            <div class="vb-what-cell">
              <span class="vb-what-emoji">🔒</span>
              <div><div class="vb-what-label breaker">Breaker Agent</div><div class="vb-what-text">${summary.breaker}</div></div>
            </div>
          </div>
        </div>`
      : null}

    <!-- sandbox files (real testing_env tree) -->
    <details class="vb-sandbox">
      <summary>
        <span>📁</span>
        <span class="vb-sandbox-name">Sandbox files</span>
        <span class="vb-sandbox-hint">· the workspace both agents act in (evidence, not the star)</span>
        <span class="vb-sandbox-toggle">click to expand</span>
      </summary>
      <${SandboxTree} refreshKey=${anyRunning ? "run" : runSeq} />
    </details>
  </section>`;
}
