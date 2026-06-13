// Minimal markdown -> React nodes (no innerHTML/XSS) plus a typewriter that
// reveals the text as it renders. Asterisk emphasis only (what Claude emits);
// avoids lookbehind so it parses on every browser.
import { e, useState, useEffect } from "./ui.js";

// Ordered so `code` and links win before emphasis.
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

// Reveals text, rendering markdown as it goes. `onGrow` (optional) fires after
// each reveal step so a container can follow the growing text (otherwise a long
// final message types out below the fold and reads as truncated).
export function Typewriter({ text, speed = 10, onGrow }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    if (!text) return;
    let i = 0;
    const id = setInterval(() => {
      i += Math.max(1, Math.round(text.length / 400)); // finish long text in ~4s
      setN(Math.min(i, text.length));
      if (onGrow) onGrow();
      if (i >= text.length) clearInterval(id);
    }, speed);
    return () => clearInterval(id);
  }, [text]);
  return e("div", { className: "md" }, renderMarkdown(text.slice(0, n)));
}
