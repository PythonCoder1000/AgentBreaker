// The static marketing sections of the Vault Boundary page, built to the redesign
// handoff (prototype/Vault Boundary.dc.html): nav, hero, "01 the problem", "03 how
// it works" (four guarantees), "04 compare" (vs. the usual defenses), a
// transparency block, and the run-it/footer. These are presentation only — the
// live demo (demo.js) carries the real backend wiring. The one piece of local
// interactivity here is the tamper-evident audit-log illustration, a self-contained
// teaching toy (separate from the live demo's "Verify audit chain" button, which
// hits the real /api/audit endpoint).
import { html, useState } from "./ui.js";

const Mark = () => html`<span class="vb-logo-mark">▮</span>`;

// Numbered section eyebrow: blue index · faint slash · muted label (redesign).
function Eyebrow({ n, label }) {
  return html`<div class="vb-eyebrow">
    <span class="vb-eyebrow-num">${n}</span>
    <span class="vb-eyebrow-sep">/</span>
    <span class="vb-eyebrow-label">${label}</span>
  </div>`;
}

export function Nav() {
  return html`<nav class="vb-nav">
    <div class="vb-nav-inner">
      <div class="vb-logo"><${Mark} /> Vault Boundary</div>
      <div class="vb-nav-links">
        <a class="vb-nav-link" href="#demo">Live demo</a>
        <a class="vb-nav-link" href="#how">How it works</a>
        <a class="vb-nav-link" href="#compare">Compare</a>
        <a class="vb-nav-link" href="https://github.com/PythonCoder1000/AgentBreaker">GitHub ↗</a>
      </div>
      <a class="vb-cta" href="#demo">▶ Run the demo</a>
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
    <div class="vb-pill"><span class="vb-pill-dot"></span> Live security demo · built at AGI House</div>
    <h1 class="vb-h1">An agent cannot leak a key<br />it never holds.</h1>
    <p class="vb-lead">Modern agents load API keys straight into their context to make calls. From that moment, one hidden instruction in a doc, email, or web page can walk off with the secret. Vault Boundary keeps the key out of the model entirely. A security layer makes every authenticated call and hands the agent <em>only the result.</em></p>
    <div class="vb-hero-sub"><span class="vb-hero-sub-dot"></span> The same attack, two agents. One leaks the key. One has nothing to leak.</div>
    <div class="vb-hero-ctas">
      <a class="vb-cta-lg" href="#demo">▶ Run the live demo</a>
      <a class="vb-cta-ghost" href="#how">See how it works</a>
    </div>
    <div class="vb-flow">
      ${node("🎫", "Scoped token", "Proves the agent may act, for this task only.")}
      ${arrow()}
      ${node("🛡️", "Security layer", "Holds the real key and makes the call.")}
      ${arrow()}
      ${node("📦", "Result only", "The answer returns. The key never does.")}
      ${arrow()}
      ${node("🔒", "Key-free model", "No secret ever sits in the context.", true)}
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
  return html`<section id="problem" class="vb-section">
    <div class="vb-section-head">
      <${Eyebrow} n="01" label="The problem" />
      <h2 class="vb-h2">The key is in the context. That is the whole problem.</h2>
    </div>
    <div class="vb-beats">
      ${beat("problem", "⚠️", "Problem", "Secrets live where the model can read them.", "To call an API, a normal agent loads the key into its context. From that point it is one injected instruction away from being stolen.")}
      ${beat("approach", "🛡️", "Approach", "Hand out access, not keys.", "Access is issued at runtime, scoped to the task. The agent acts through a security layer that holds the key for it.")}
      ${beat("payoff", "✅", "Payoff", "Nothing to steal.", "A fully hijacked agent cannot leak a secret it was never given. The theft simply has no target.")}
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// 03 — How it works (four guarantees, enforced in code)
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
    <p class="vb-details-p">Each decision is HMAC-chained to the one before it. An attacker who breaches the layer and tries to slip in a <code>cat .env</code> would still have to forge every following hash. Alter one record and the chain stops verifying. Try it.</p>
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

function Panel({ n, title, meta, children }) {
  return html`<details class="vb-details">
    <summary>
      <span class="vb-details-num">${n}</span>
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
    <p class="vb-details-p">The token says the agent <em>may</em>. The security layer decides <em>how</em>, without the agent ever holding the key.</p>
    <div class="vb-steps">
      ${step("🎫", "Token presented", "scope is checked first")}
      ${arrow()}
      ${step("🔑", "Layer leases key", "at runtime, server-side")}
      ${arrow()}
      ${step("📡", "Call is made", "the key authenticates it")}
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

function CapabilityTokenIllustration() {
  return html`<div>
    <p class="vb-details-p">Every agent carries a signed token that lists exactly which tools, paths, and recipients it may touch, plus an expiry. The layer checks it before every call, and a single revoke cuts off the agent instantly.</p>
    <div class="vb-captoken">
      <div class="vb-captoken-head">
        <span class="vb-captoken-title">Capability token</span>
        <span class="vb-captoken-status">ACTIVE</span>
      </div>
      <div class="vb-captoken-grid">
        <span class="vb-captoken-k">tools</span><span class="vb-captoken-v mono">run_bash · send_email · call_api</span>
        <span class="vb-captoken-k">paths</span><span class="vb-captoken-v mono">testing_env/** (read), data/** (write)</span>
        <span class="vb-captoken-k">recipients</span><span class="vb-captoken-v mono">*@horizon.org</span>
        <span class="vb-captoken-k">expiry</span><span class="vb-captoken-v mono">15m · delegate ≤ 1</span>
      </div>
    </div>
    <p class="vb-details-note" style=${{ fontStyle: "normal", color: "#7a8699" }}>A call that falls outside the token is rejected before it runs. There is no prompt to talk around.</p>
  </div>`;
}

function DelegationIllustration() {
  return html`<div>
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
  </div>`;
}

export function HowItWorks() {
  return html`<section id="how" class="vb-section narrow">
    <div class="vb-section-head">
      <${Eyebrow} n="03" label="How it works" />
      <h2 class="vb-h2">Four guarantees, enforced in code.</h2>
      <p class="vb-section-sub">Each panel is collapsed by default. Skip it for the gist, or open it to verify.</p>
    </div>
    <div class="vb-details-stack">
      <${Panel} n="01" title="The security layer holds the key, not the agent." meta="5 steps"><${AccessLayerSteps} /></${Panel}>
      <${Panel} n="02" title="Capability tokens prove what an agent may do." meta="signed · revocable"><${CapabilityTokenIllustration} /></${Panel}>
      <${Panel} n="03" title="A sub-agent can only ever shrink the scope." meta="scope shrinks"><${DelegationIllustration} /></${Panel}>
      <${Panel} n="04" title="Every call is hash-chained to the last." meta="try breaking it"><${AuditIllustration} /></${Panel}>
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// 04 — Compare (why the usual defenses still leak)
// ---------------------------------------------------------------------------
const COMPARE_ROWS = [
  { name: "Prompt guardrails", idea: "Tell the model, in its system prompt, not to reveal secrets.", where: "In the model context, as readable text.", verdict: "Talked into ignoring the rule. Leaks.", tone: "bad" },
  { name: "Secrets manager", idea: "Store keys in a vault and fetch them at runtime.", where: "Fetched into the agent context to use.", verdict: "Readable once fetched. Leaks.", tone: "bad" },
  { name: "Output filtering / DLP", idea: "Scan agent output and redact anything secret-shaped.", where: "In the context, filtered on the way out.", verdict: "Encoded or paraphrased past the filter.", tone: "warn" },
  { name: "Vault Boundary", idea: "A security layer makes the call and returns only the result.", where: "Never within the agent reach.", verdict: "Nothing to hand over. Holds.", tone: "good" },
];

export function Compare() {
  return html`<section id="compare" class="vb-section narrow">
    <div class="vb-section-head">
      <${Eyebrow} n="04" label="Compare" />
      <h2 class="vb-h2">Why the usual defenses still leak.</h2>
      <p class="vb-section-sub">They all leave the key somewhere the model can read. Vault Boundary does not.</p>
    </div>
    <div class="vb-cmp">
      <div class="vb-cmp-head">
        <div>Approach</div><div>The idea</div><div>Where the key lives</div><div>Under prompt injection</div>
      </div>
      ${COMPARE_ROWS.map((r) => html`<div key=${r.name} class=${"vb-cmp-row " + r.tone}>
        <div class="vb-cmp-name">${r.name}</div>
        <div class="vb-cmp-idea">${r.idea}</div>
        <div class="vb-cmp-where">${r.where}</div>
        <div class=${"vb-cmp-verdict " + r.tone}>${r.verdict}</div>
      </div>`)}
    </div>
    <div class="vb-trio">
      <div class="vb-trio-card"><div class="vb-trio-kicker">AGENCY</div><p>Scoped, revocable tokens decide what an agent, or its sub-agents, may even attempt.</p></div>
      <div class="vb-trio-card"><div class="vb-trio-kicker">ACCESS</div><p>The security layer leases credentials at runtime, so the secret never enters the model's context.</p></div>
      <div class="vb-trio-card"><div class="vb-trio-kicker">ACCOUNTABILITY</div><p>A hash-chained log records who reached what, by reference and fingerprint, never by value.</p></div>
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Transparency — what's real vs. simulated + known limits
// ---------------------------------------------------------------------------
export function Transparency() {
  return html`<section class="vb-section narrow" style=${{ paddingTop: "46px" }}>
    <div class="vb-pair">
      <div class="vb-honesty-card">
        <div class="vb-honesty-head"><span>⚖️</span><h3>What is real vs. simulated</h3></div>
        <div class="vb-realsim-cols">
          <div>
            <div class="vb-realsim-label real">Real</div>
            <ul>
              <li>The agent loop and policy core</li>
              <li>The layer, broker, and audit chain</li>
              <li>Capability tokens and revocation</li>
            </ul>
          </div>
          <div>
            <div class="vb-realsim-label sim">Simulated</div>
            <ul>
              <li>No real mail is sent</li>
              <li>The credential is synthetic</li>
              <li>External services are stand-ins</li>
            </ul>
          </div>
        </div>
      </div>
      <div class="vb-honesty-card">
        <div class="vb-honesty-head"><span>🔍</span><h3>Known limits</h3></div>
        <ul>
          <li>The audit HMAC key is process-local.</li>
          <li>Truncating the newest records leaves a valid prefix. Verify returns the checked length so a caller can catch it.</li>
          <li>The interceptor fails open by default. Deterministic hard blocks still apply.</li>
        </ul>
      </div>
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Run it + footer
// ---------------------------------------------------------------------------
export function Footer() {
  return html`<section class="vb-section narrow" style=${{ paddingTop: "30px" }}>
    <div class="vb-runit" id="run">
      <h3 class="vb-runit-title">Run the whole thing yourself.</h3>
      <p class="vb-runit-lead">Python 3.11+, FastAPI and SSE, a no-build React frontend. Claude Sonnet drives both the agent loop and the policy evaluator. Set ANTHROPIC_API_KEY in .env first.</p>
      <div class="vb-runit-cli">
        <div class="vb-cli"><span class="vb-prompt">$</span> git clone https://github.com/PythonCoder1000/AgentBreaker</div>
        <div class="vb-cli"><span class="vb-prompt">$</span> uv sync --extra web</div>
        <div class="vb-cli"><span class="vb-prompt">$</span> uv run python web/serve.py</div>
      </div>
      <div class="vb-runit-ctas">
        <a class="vb-cta-lg" href="https://github.com/PythonCoder1000/AgentBreaker">View on GitHub ↗</a>
        <a class="vb-cta-ghost" href="#demo">▶ Back to the demo</a>
      </div>
    </div>

    <div class="vb-footer">
      <div class="vb-footer-brand"><span class="vb-footer-mark">▮</span> Vault Boundary</div>
      <span class="vb-footer-sep">·</span>
      <span class="vb-footer-tag">An agent should never hold the keys.</span>
      <div class="vb-footer-links">
        <a href="https://github.com/PythonCoder1000/AgentBreaker">GitHub ↗</a>
        <a href="#demo">Live demo ↗</a>
        <a href="#how">How it works ↗</a>
      </div>
    </div>
    <div class="vb-footer-note">Built at AGI House Agent Identity Build Day · Authorized testing only · Everything acted on is synthetic.</div>
  </section>`;
}
