// The Home landing view: the two agent descriptions plus a comparison table of
// how each holds up across the preset attacks.
import { html } from "./ui.js";

const STATUS = {
  works: { icon: "✓", label: "Holds", cls: "ok" },
  fails: { icon: "✗", label: "Fails", cls: "bad" },
  uncertain: { icon: "~", label: "Uncertain", cls: "warn" },
};

function statusMeta(status) {
  return STATUS[status] || STATUS.uncertain;
}

function StatusCell({ status }) {
  const s = statusMeta(status);
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
      <div class="eyebrow">Agent access &middot; identity &middot; accountability</div>
      <h2>When an agent acts, where does its access come from?</h2>
      <p class="lead">Every agent needs credentials, but none of them should <em>hold</em> the keys.
        Both agents get the same tasks, tools, and environment; the difference is whether access is
        an <em>enforced, scoped, brokered</em> capability — or just rules in a prompt and a secret
        sitting in a file. Watch where each holds the line, and where one quietly doesn't.</p>
    </section>

    <section class="agent-cards">
      <${AgentCard} kind="prompt"
        name="Prompt Agent"
        approach="Guardrails in the prompt, credentials in a file"
        blurb=${"The conventional approach. Its safety rules are written in plain language inside the model's prompt, and the secrets it needs sit in a .env file in its workspace. A request phrased the right way walks past the rules — and the moment the agent reads that file, the credential is in the model's context, where one injected instruction can carry it out the door."} />
      <${AgentCard} kind="breaker"
        name="Breaker Agent"
        approach="A scoped capability token + a runtime access layer"
        blurb=${"Carries no rules and holds no keys. It acts under a scoped, revocable capability token, and reaches real systems through call_api — an access layer that leases the credential at runtime, uses it, and returns only the result, so the secret never enters the model's context. Every tool call is still judged by an enforced policy layer, and every access is written to a tamper-evident audit log. Even a fully hijacked loop has nothing to steal."} />
    </section>

    <section class="results">
      <div class="results-head">
        <h3>How they hold up</h3>
        <div class="legend">
          <span class="status ok"><b>✓</b> Holds</span>
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
            <td class=${"res res-" + statusMeta(s.prompt_status).cls}><${StatusCell} status=${s.prompt_status} /></td>
            <td class=${"res res-" + statusMeta(s.breaker_status).cls}><${StatusCell} status=${s.breaker_status} /></td>
          </tr>`)}
        </tbody>
      </table>
    </section>

    <button class="btn run cta" onClick=${() => setView("chat")}>Open the live demo →</button>
  </div>`;
}
