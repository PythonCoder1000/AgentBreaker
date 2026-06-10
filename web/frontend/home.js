// The Home landing view: the two agent descriptions plus a comparison table of
// how each holds up across the preset attacks.
import { html } from "./ui.js";

const STATUS = {
  works: { icon: "✓", label: "Holds", cls: "ok" },
  fails: { icon: "✗", label: "Fails", cls: "bad" },
  uncertain: { icon: "~", label: "Uncertain", cls: "warn" },
};

function StatusCell({ status }) {
  const s = STATUS[status] || STATUS.uncertain;
  return html`<span class=${"status " + s.cls}><b>${s.icon}</b> ${s.label}</span>`;
}

function AgentCard({ kind, name, approach, blurb }) {
  return html`<div class=${"agent-card " + kind}>
    <div class="ac-head">
      <span class="ac-dot"></span>
      <div>
        <div class="ac-name">${name}</div>
        <div class="ac-approach">${approach}</div>
      </div>
    </div>
    <p class="ac-blurb">${blurb}</p>
  </div>`;
}

export function HomeView({ scenarios, setView }) {
  return html`<div class="home">
    <section class="hero">
      <div class="eyebrow">Agent security · live comparison</div>
      <h2>Prompt rules vs. an enforced policy layer</h2>
      <p class="lead">Both agents get the same tasks, the same tools, and the same environment.
        The only thing that differs is <em>how</em> each one is kept in check — so you can watch
        where each holds the line, and where one quietly doesn't.</p>
    </section>

    <section class="agent-cards">
      <${AgentCard} kind="prompt"
        name="Prompt Agent"
        approach="Guardrails written into the system prompt"
        blurb=${"The conventional approach. Its safety rules are spelled out in plain language inside the model's prompt, and the agent is asked to follow them. That covers the cases the rules anticipated — but those rules live inside the very model they're meant to restrain, so a request phrased the right way can walk straight past them."} />
      <${AgentCard} kind="breaker"
        name="Breaker Agent"
        approach="An enforced policy layer outside the model"
        blurb=${"Carries no safety rules in its prompt at all. Instead, every tool call is intercepted and judged by a dedicated security framework: deterministic blocks for known-dangerous actions, plus a content-aware review that reads what a call would actually expose. Enforcement never depends on the model choosing to comply."} />
    </section>

    <section class="results">
      <div class="results-head">
        <h3>How they hold up</h3>
        <div class="legend">
          <span class="status ok"><b>✓</b> Holds</span>
          <span class="status warn"><b>~</b> Uncertain</span>
          <span class="status bad"><b>✗</b> Fails</span>
        </div>
      </div>
      <table class="compare">
        <thead><tr>
          <th>Scenario</th>
          <th class="c-prompt">Prompt Agent</th>
          <th class="c-breaker">Breaker Agent</th>
        </tr></thead>
        <tbody>
          ${scenarios.map((s) => html`<tr key=${s.id}>
            <td class="atk"><b>${s.name}</b><span class="atk-sub">${s.tagline}</span></td>
            <td><${StatusCell} status=${s.prompt_status} /></td>
            <td><${StatusCell} status=${s.breaker_status} /></td>
          </tr>`)}
        </tbody>
      </table>
    </section>

    <button class="btn run cta" onClick=${() => setView("chat")}>Open the live demo →</button>
  </div>`;
}
