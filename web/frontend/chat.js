// The live chat view: preset/run controls, the file explorer, the two agent
// columns, and the free-text composer.
import { html, useState } from "./ui.js";
import { Column } from "./feed.js";
import { FileExplorer } from "./explorer.js";

export function ChatView({ scenarios, selected, setSelected, running, feeds, input, setInput,
                           runScenario, sendMessage, newSession, onDecide }) {
  const [showFiles, setShowFiles] = useState(true);
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
      <button class="btn ghost" onClick=${() => setShowFiles((s) => !s)}>${showFiles ? "Hide files" : "📁 Files"}</button>
      <div class="kbd"><span><b>N</b> next</span><span><b>R</b> replay</span></div>
    </div>

    ${current ? html`<div class="scenario-bar"><b>Preset:</b> ${current.task}</div>` : null}

    <div class="workspace">
      ${showFiles ? html`<${FileExplorer} running=${running} />` : null}
      <div class="columns">
        <${Column} kind="prompt" title="Prompt Agent" events=${feeds.prompt} onDecide=${onDecide} />
        <${Column} kind="breaker" title="Breaker Agent" events=${feeds.breaker} onDecide=${onDecide} />
      </div>
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
