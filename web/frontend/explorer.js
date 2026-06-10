// Read-only file explorer for the testing_env tree. Refreshes after each turn
// so the user can watch the workspace change. Click a file to view its contents
// in an overlay. Secret-looking entries are flagged.
import { html, fmtSize, useState, useEffect, useCallback } from "./ui.js";

const SECRET_RE = /(^\.env|credential|secret|\.key$|\.pem$|\.p12$)/i;

function FileNode({ node, path, depth, onOpen }) {
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
        ? html`<ul class="fchildren">${(node.children || []).map((c, i) => html`<${FileNode} key=${i} node=${c} path=${path + "/" + c.name} depth=${depth + 1} onOpen=${onOpen} />`)}</ul>`
        : null}
    </li>`;
  }
  const secret = SECRET_RE.test(node.name);
  return html`<li>
    <div class=${"frow file" + (secret ? " secret" : "")} style=${pad} title="View contents" onClick=${() => onOpen(path)}>
      <span class="caret"></span>
      <span class="ficon">${secret ? "🔑" : "📄"}</span>
      <span class="fname">${node.name}</span>
      <span class="fsize">${fmtSize(node.size)}</span>
    </div>
  </li>`;
}

function FileViewer({ path, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setData(null);
    setErr(null);
    fetch("/api/file?path=" + encodeURIComponent(path))
      .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(j.detail || "could not read file"))))
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, [path]);
  useEffect(() => {
    const onKey = (ev) => ev.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const name = path.split("/").pop();
  return html`<div class="viewer-overlay" onClick=${onClose}>
    <div class="viewer" onClick=${(ev) => ev.stopPropagation()}>
      <div class="viewer-head">
        <span class="ficon">📄</span>
        <span class="viewer-name">${name}</span>
        ${data ? html`<span class="viewer-size">${fmtSize(data.size)}</span>` : null}
        <button class="viewer-close" title="Close (Esc)" onClick=${onClose}>✕</button>
      </div>
      <div class="viewer-body">
        ${err
          ? html`<div class="err">${err}</div>`
          : !data
          ? html`<div class="empty">loading…</div>`
          : data.binary
          ? html`<div class="empty">Binary file — can't display.</div>`
          : html`<pre class="viewer-pre">${data.content}${data.truncated ? "\n\n… truncated …" : ""}</pre>`}
      </div>
    </div>
  </div>`;
}

export function FileExplorer({ running }) {
  const [tree, setTree] = useState(null);
  const [viewing, setViewing] = useState(null);
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
        ? html`<ul class="ftree">${(tree.children || []).map((c, i) => html`<${FileNode} key=${i} node=${c} path=${c.name} depth=${0} onOpen=${setViewing} />`)}</ul>`
        : html`<div class="empty">loading…</div>`}
    </div>
    ${viewing ? html`<${FileViewer} path=${viewing} onClose=${() => setViewing(null)} />` : null}
  </aside>`;
}
