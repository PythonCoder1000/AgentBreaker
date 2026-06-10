// Read-only file explorer for the testing_env tree. Refreshes after each turn
// so the user can watch the workspace change. Secret-looking entries are flagged.
import { html, fmtSize, useState, useEffect, useCallback } from "./ui.js";

const SECRET_RE = /(^\.env|credential|secret|\.key$|\.pem$|\.p12$)/i;

function FileNode({ node, depth }) {
  const [open, setOpen] = useState(depth < 1);
  const pad = { paddingLeft: depth * 14 + 8 + "px" };
  if (node.type === "dir") {
    const secret = /secret/i.test(node.name);
    return html`<li>
      <div class=${"frow dir" + (secret ? " secret" : "")} style=${pad} onClick=${() => setOpen((o) => !o)}>
        <span class="caret">${open ? "▾" : "▸"}</span>
        <span class="ficon">${open ? "📂" : "📁"}</span>
        <span class="fname">${node.name}</span>
      </div>
      ${open
        ? html`<ul class="fchildren">${(node.children || []).map((c, i) => html`<${FileNode} key=${i} node=${c} depth=${depth + 1} />`)}</ul>`
        : null}
    </li>`;
  }
  const secret = SECRET_RE.test(node.name);
  return html`<li>
    <div class=${"frow file" + (secret ? " secret" : "")} style=${pad}>
      <span class="caret"></span>
      <span class="ficon">${secret ? "🔑" : "📄"}</span>
      <span class="fname">${node.name}</span>
      <span class="fsize">${fmtSize(node.size)}</span>
    </div>
  </li>`;
}

export function FileExplorer({ running }) {
  const [tree, setTree] = useState(null);
  const load = useCallback(() => {
    fetch("/api/files").then((r) => r.json()).then(setTree).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!running) load(); }, [running, load]); // refresh after each turn
  return html`<aside class="explorer">
    <div class="explorer-head">
      <span class="ficon">📁</span><span>testing_env</span>
      <button class="refresh" title="Refresh" onClick=${load}>↻</button>
    </div>
    <div class="explorer-body">
      ${tree
        ? html`<ul class="ftree">${(tree.children || []).map((c, i) => html`<${FileNode} key=${i} node=${c} depth=${0} />`)}</ul>`
        : html`<div class="empty">loading…</div>`}
    </div>
  </aside>`;
}
