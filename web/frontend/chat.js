// The live chat view: preset/run controls, the file explorer, and the two agent
// columns. Each column carries its own composer so messages can be sent to one
// agent at a time.
import { html, useState } from "./ui.js";
import { Column } from "./feed.js";
import { FileExplorer } from "./explorer.js";
import { TrustChain } from "./trustchain.js";

export function ChatView({ scenarios, selected, setSelected, running, feeds, runSeq,
                           identity, scans, runScenario, sendToAgent, newSession,
                           onDecide, onRevoke, onVerifyAudit }) {
  const [showFiles, setShowFiles] = useState(true);
  const current = scenarios[selected];
  const anyRunning = running.prompt || running.breaker;
  return html`<div class="chat">
    <div class="controls-bar">
      <div class="select-wrap">
        <label>Preset attack</label>
        <select value=${selected} onChange=${(e) => setSelected(Number(e.target.value))}>
          ${scenarios.map((s, i) => html`<option key=${s.id} value=${i}>${s.name}</option>`)}
        </select>
      </div>
      <button class="btn run" disabled=${anyRunning || !scenarios.length} onClick=${() => runScenario(selected)}>
        ${anyRunning ? "RUNNING…" : "▶ RUN"}
      </button>
      <button class="btn ghost" disabled=${anyRunning} onClick=${newSession}>+ New session</button>
      <button class="btn ghost" onClick=${() => setShowFiles((s) => !s)}>${showFiles ? "Hide files" : "📁 Files"}</button>
      <div class="kbd"><span><b>N</b> next</span><span><b>R</b> replay</span></div>
    </div>

    ${current ? html`<div class="scenario-bar"><b>Preset:</b> ${current.task}</div>` : null}

    <div class="workspace">
      ${showFiles ? html`<${FileExplorer} running=${anyRunning} />` : null}
      <div class="columns">
        <${Column} kind="prompt" title="Prompt Agent" events=${feeds.prompt} resetKey=${runSeq}
          running=${running.prompt} scan=${scans && scans.prompt}
          onSend=${(text) => sendToAgent("prompt", text)} onDecide=${onDecide} />
        <div class="breaker-panel">
          <${TrustChain} token=${identity && identity.breaker}
            onRevoke=${() => onRevoke("breaker")} onVerifyAudit=${onVerifyAudit} />
          <${Column} kind="breaker" title="Breaker Agent" events=${feeds.breaker} resetKey=${runSeq}
            running=${running.breaker} scan=${scans && scans.breaker}
            onSend=${(text) => sendToAgent("breaker", text)} onDecide=${onDecide} />
        </div>
      </div>
    </div>
  </div>`;
}
