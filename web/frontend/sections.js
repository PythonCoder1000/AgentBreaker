// The static marketing sections of the Vault Boundary page: nav, hero, the three
// beats, the "proof & how it works" panels, the comparison, and the footer. These
// are presentation only — the live demo (demo.js) carries the real backend wiring.
// The one piece of local interactivity here is the tamper-evident audit-log
// illustration, a self-contained teaching toy (separate from the live demo's
// "Verify audit chain" button, which hits the real /api/audit endpoint).
import { html, useState } from "./ui.js";

const Mark = () => html`<span class="vb-logo-mark">▮</span>`;

export function Nav() {
  return html`<nav class="vb-nav">
    <div class="vb-nav-inner">
      <div class="vb-logo"><${Mark} /> Vault Boundary</div>
      <div class="vb-nav-links">
        <a class="vb-nav-link" href="#demo">Live demo</a>
        <a class="vb-nav-link" href="#how">How it works</a>
        <a class="vb-nav-link" href="#different">Why different</a>
      </div>
      <a class="vb-cta" href="#demo">▶ Open the demo</a>
    </div>
  </nav>`;
}

export function Hero() {
  const node = (icon, title, desc, clean) => html`<div class=${"vb-flow-node" + (clean ? " clean" : "")}>
    <div class="vb-flow-icon">${icon}</div>
    <div class="vb-flow-title">${title}</div>
    <div class="vb-flow-desc">${desc}</div>
  </div>`;
  const arrow = () => html`<div class="vb-flow-arrow">→</div>`;
  return html`<section class="vb-section narrow-hero vb-hero">
    <div class="vb-pill"><span class="vb-pill-dot"></span> Live security demo · two agents, one task</div>
    <h1 class="vb-h1">Agents shouldn't<br />hold the keys.</h1>
    <p class="vb-lead">Issue access at runtime, scoped to the task, so the secret <em>never touches the model.</em> Even a hijacked agent has nothing to steal.</p>
    <div class="vb-hero-ctas">
      <a class="vb-cta-lg" href="#demo">▶ Open the live demo</a>
      <a class="vb-cta-ghost" href="#beats">Watch the 60-sec version</a>
    </div>
    <div class="vb-flow">
      ${node("🎫", "Scoped token", "grants access for this task only")}
      ${arrow()}
      ${node("🛡️", "Access layer", "injects the key at runtime")}
      ${arrow()}
      ${node("📦", "Result only", "the answer comes back, not the key")}
      ${arrow()}
      ${node("🔒", "Clean context", "no secret ever in the model", true)}
    </div>
  </section>`;
}

export function Beats() {
  const beat = (kind, emoji, kicker, title, body) => html`<div class=${"vb-beat " + kind}>
    <div class="vb-beat-bar"></div>
    <div class="vb-beat-head"><span class="vb-beat-emoji">${emoji}</span><span class="vb-beat-kicker">${kicker}</span></div>
    <h3 class="vb-beat-title">${title}</h3>
    <p class="vb-beat-body">${body}</p>
  </div>`;
  return html`<section id="beats" class="vb-section">
    <div class="vb-section-head">
      <div class="vb-eyebrow">The idea in three beats</div>
      <h2 class="vb-h2">Problem → approach → payoff</h2>
    </div>
    <div class="vb-beats">
      ${beat("problem", "⚠️", "Problem", "The key is sitting in the prompt.", "A long-lived credential written into a prompt or a file is one bad instruction away from being stolen.")}
      ${beat("approach", "🛡️", "Approach", "Hand out access, not keys.", "Access is issued at runtime, scoped to the task. The agent acts through a layer that holds the key for it.")}
      ${beat("payoff", "✅", "Payoff", "Nothing to steal.", "Even a fully hijacked agent can't leak a secret it was never given. The theft simply has no target.")}
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Proof & how it works
// ---------------------------------------------------------------------------
const AUDIT_ROWS = [
  { seq: "01", action: "token.validate", resource: "cap_7f3a·2e9b", hash: "9f1c…" },
  { seq: "02", action: "broker.lease", resource: "helios", hash: "a37e…" },
  { seq: "03", action: "call_api", resource: "helios/health", hash: "c08b…" },
  { seq: "04", action: "policy.block", resource: "send_email", hash: "e2d4…" },
];

function AuditIllustration() {
  const [tampered, setTampered] = useState(false);
  const [chain, setChain] = useState(null); // null | "ok" | "broken"
  return html`<div>
    <p class="vb-details-p">Every decision is HMAC-chained to the one before it. Alter any record and the chain stops verifying. Provably.</p>
    <div class="vb-audit-table">
      <div class="vb-audit-thead"><span>#</span><span>action</span><span>resource</span><span>hash</span></div>
      ${AUDIT_ROWS.map((r, i) => {
        const t = tampered && i === 2;
        return html`<div key=${r.seq} class=${"vb-audit-trow" + (t ? " tampered" : "")}>
          <span class="vb-audit-seq">${r.seq}</span>
          <span class="vb-audit-action">${r.action}</span>
          <span class="vb-audit-resource">${r.resource}</span>
          <span class="vb-audit-hash">${t ? "c0ff…" : r.hash}</span>
        </div>`;
      })}
    </div>
    <div class="vb-audit-actions">
      <button class="vb-verify-btn" onClick=${() => setChain(tampered ? "broken" : "ok")}>Verify chain</button>
      <button class="vb-tamper-btn" onClick=${() => { setTampered((t) => !t); setChain(null); }}>${tampered ? "Restore record #3" : "Tamper with record #3"}</button>
      ${chain ? html`<span class=${"vb-chain-msg " + (chain === "broken" ? "bad" : "ok")}>${chain === "broken" ? "✗ chain broken at record #3" : "✓ chain intact: 4 records verified"}</span>` : null}
    </div>
  </div>`;
}

function Panel({ emoji, title, meta, children }) {
  return html`<details class="vb-details">
    <summary>
      <span class="vb-details-emoji">${emoji}</span>
      <span class="vb-details-title">${title}</span>
      <span class="vb-details-meta">${meta}</span>
      <span class="vb-details-plus">＋</span>
    </summary>
    <div class="vb-details-body">${children}</div>
  </details>`;
}

function AccessLayerSteps() {
  const step = (icon, title, desc, good) => html`<div class=${"vb-step" + (good ? " good" : "")}>
    <div class="vb-step-icon">${icon}</div>
    <div class="vb-step-title">${title}</div>
    <div class="vb-step-desc">${desc}</div>
  </div>`;
  const arrow = () => html`<div class="vb-step-arrow">→</div>`;
  return html`<div>
    <p class="vb-details-p">The token says you <em>may</em>. The broker decides <em>how</em>, without the agent ever holding the key.</p>
    <div class="vb-steps">
      ${step("🎫", "Token presented", "scope checked first")}
      ${arrow()}
      ${step("🔑", "Broker leases key", "at runtime, server-side")}
      ${arrow()}
      ${step("📡", "Call is made", "key authenticates it")}
    </div>
    <div class="vb-steps">
      ${step("📦", "Result returned", "only the answer, never the key", true)}
      ${arrow()}
      ${step("🧾", "Audit appended", "by reference, not value")}
      <div></div><div></div>
    </div>
    <p class="vb-details-note">The <code>call_api</code> schema has no field for a credential, so the model literally cannot supply, read, or receive one.</p>
  </div>`;
}

export function HowItWorks() {
  return html`<section id="how" class="vb-section narrow">
    <div class="vb-section-head">
      <div class="vb-eyebrow">Proof &amp; how it works</div>
      <h2 class="vb-h2">Believe the demo? Here's the machinery.</h2>
      <p class="vb-section-sub">Each panel is collapsed by default. Skip it for the gist, or open it to verify.</p>
    </div>
    <div class="vb-details-stack">
      <${Panel} emoji="🛡️" title="How the access layer works" meta="5 steps"><${AccessLayerSteps} /></${Panel}>

      <${Panel} emoji="🌳" title="How delegation stays safe" meta="scope shrinks">
        <p class="vb-details-p">A sub-agent's token is <em>derived</em> by intersecting its parent's scope. A child can never out-reach its parent, and revoking the root blocks the whole subtree.</p>
        <div class="vb-deleg">
          <div class="vb-deleg-card root">
            <div class="vb-deleg-title">root token</div>
            <div class="vb-deleg-scope">run_bash · send_email · call_api · paths: testing_env/** · delegate ≤ 1</div>
          </div>
          <div class="vb-deleg-line"></div>
          <div class="vb-deleg-card child">
            <div class="vb-deleg-title">sub-agent token <span class="vb-deleg-derived">(derived)</span></div>
            <div class="vb-deleg-scope">call_api only · paths: testing_env/data/** · <span class="vb-deleg-cut">delegate ✕</span></div>
          </div>
        </div>
        <p class="vb-details-note" style=${{ fontStyle: "normal", color: "#7a8699" }}>Notice what fell away: no more <code>send_email</code>, paths narrowed, delegation cut to zero.</p>
      </${Panel}>

      <${Panel} emoji="🧾" title="The tamper-evident audit log" meta="try breaking it"><${AuditIllustration} /></${Panel}>

      <${Panel} emoji="⚖️" title="What's real vs. simulated" meta="no hand-waving">
        <div class="vb-pair">
          <div class="vb-realsim real">
            <div class="vb-realsim-head"><span>✓</span> Real</div>
            <ul>
              <li>The agent loop, tools, and policy core</li>
              <li><code>run_bash</code> really executes in the sandbox</li>
              <li>The interceptor, broker, inspector &amp; audit chain</li>
              <li>Capability tokens, scope &amp; revocation</li>
            </ul>
          </div>
          <div class="vb-realsim sim">
            <div class="vb-realsim-head"><span>◑</span> Simulated</div>
            <ul>
              <li>No real mail is sent; replies are scripted</li>
              <li>The credential is synthetic (real key drops in via env var)</li>
              <li>The workspace is seeded decoy data</li>
              <li>External services are stand-ins</li>
            </ul>
          </div>
        </div>
      </${Panel}>
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Why it's different
// ---------------------------------------------------------------------------
const COMPARE_ROWS = [
  { q: "Where do the rules live?", prompt: "In the system prompt, as advisory text.", breaker: "In an enforced policy layer that runs before each call." },
  { q: "Where does the key live?", prompt: "In a file the agent can read.", breaker: "Nowhere the agent can reach; it is leased at runtime." },
  { q: "What can a prompt injection do?", prompt: "Talk the agent into leaking the secret.", breaker: "Nothing: there is no secret to hand over." },
  { q: "Who can prove what happened?", prompt: "No one: there is no trustworthy record.", breaker: "Anyone: via the tamper-evident audit chain." },
];

export function WhyDifferent() {
  return html`<section id="different" class="vb-section narrow">
    <div class="vb-section-head">
      <div class="vb-eyebrow">Why it's different</div>
      <h2 class="vb-h2">Prompt rules vs. an enforced access layer</h2>
    </div>
    <div class="vb-compare">
      <div class="vb-compare-head">
        <div class="vb-compare-q">The question</div>
        <div class="vb-compare-agent prompt"><span class="vb-swatch"></span>Prompt Agent</div>
        <div class="vb-compare-agent breaker"><span class="vb-swatch"></span>Breaker Agent</div>
      </div>
      ${COMPARE_ROWS.map((r, i) => html`<div key=${i} class="vb-compare-row">
        <div class="vb-compare-cell-q">${r.q}</div>
        <div class="vb-compare-cell-prompt">${r.prompt}</div>
        <div class="vb-compare-cell-breaker">${r.breaker}</div>
      </div>`)}
    </div>
    <div class="vb-trio">
      <div class="vb-trio-card"><div class="vb-trio-kicker">AGENCY</div><p>Scoped, revocable tokens decide what an agent, or its sub-agents, may even attempt.</p></div>
      <div class="vb-trio-card"><div class="vb-trio-kicker">ACCESS</div><p>The broker leases credentials at runtime, so the secret never enters the model's context.</p></div>
      <div class="vb-trio-card"><div class="vb-trio-kicker">ACCOUNTABILITY</div><p>A tamper-evident chain records who reached what, by reference and fingerprint, never by value.</p></div>
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Honesty + run-it + footer
// ---------------------------------------------------------------------------
export function Footer() {
  return html`<section class="vb-section narrow" style=${{ paddingTop: "40px" }}>
    <div class="vb-pair">
      <div class="vb-honesty-card">
        <div class="vb-honesty-head"><span>🔍</span><h3>Known limits</h3></div>
        <ul>
          <li>The audit HMAC key is process-local.</li>
          <li>Truncating the newest records leaves a valid prefix; verify returns the verified length so a caller can catch it.</li>
          <li>The interceptor fails open by default; deterministic hard blocks still apply.</li>
        </ul>
      </div>
      <div class="vb-honesty-card dark">
        <div class="vb-honesty-head"><span>▶</span><h3>Run it yourself</h3></div>
        <div class="vb-cli"><span class="vb-prompt">$</span> uv run python web/serve.py</div>
        <p class="vb-runit-note">Python 3.11+ · FastAPI + SSE · no-build React frontend · Claude Sonnet drives both the agent loop and the policy evaluator.</p>
      </div>
    </div>
    <div class="vb-footer">
      <div class="vb-footer-brand"><span class="vb-footer-mark">▮</span> Vault Boundary</div>
      <span class="vb-footer-sep">·</span>
      <span class="vb-footer-tag">An agent should never hold the keys.</span>
      <div class="vb-footer-links">
        <a href="#">GitHub ↗</a>
        <a href="#demo">Live demo ↗</a>
        <a href="#">60-sec video ↗</a>
      </div>
    </div>
    <div class="vb-footer-note">Authorized testing only · everything acted on is synthetic</div>
  </section>`;
}
