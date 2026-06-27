// Trust chain panel: shows the active capability token, its scope, and lets the
// operator revoke it mid-session. Appears above the Breaker Agent's feed column.
import { html, useState } from "./ui.js";

function ScopeRow({ label, value }) {
  if (value === null || value === undefined) return null;
  return html`<div class="tc-row">
    <span class="tc-k">${label}</span>
    <span class="tc-v">${value}</span>
  </div>`;
}

export function TrustChain({ token, onRevoke, onVerifyAudit }) {
  const [localRevoked, setLocalRevoked] = useState(false);
  const [auditState, setAuditState] = useState(null); // null | "checking" | {ok,...}

  if (!token) return null;

  const verify = () => {
    if (!onVerifyAudit) return;
    setAuditState("checking");
    Promise.resolve(onVerifyAudit()).then((res) =>
      setAuditState(res || { ok: false, reason: "unavailable" }));
  };

  const isRevoked = token.revoked || localRevoked;
  const scope = token.scope || {};
  const tools = (scope.tools || []).join(", ");
  const emailTo = scope.email_to ? scope.email_to.join(", ") : "any @horizon.org";
  const paths = scope.allowed_paths ? scope.allowed_paths.join(", ") : "unrestricted";

  const handleRevoke = () => {
    if (isRevoked || !onRevoke) return;
    onRevoke();
    setLocalRevoked(true);
  };

  return html`<div class=${"trust-chain" + (isRevoked ? " revoked" : "")}>
    <div class="tc-header">
      <span class="tc-title">⬡ Identity Token</span>
      <span class=${"tc-status " + (isRevoked ? "tc-revoked" : "tc-active")}>
        ${isRevoked ? "✕ REVOKED" : "● ACTIVE"}
      </span>
    </div>
    <div class="tc-body">
      <${ScopeRow} label="Token" value=${token.token_id} />
      <${ScopeRow} label="Principal" value=${token.principal} />
      <${ScopeRow} label="Agent" value=${token.agent_name} />
      ${token.parent_token_id
        ? html`<${ScopeRow} label="Derived from" value=${token.parent_token_id} />`
        : null}
      <${ScopeRow} label="Depth" value=${"d" + token.depth + (token.depth === 0 ? " (root)" : " (sub-agent)")} />
      <div class="tc-divider"></div>
      <${ScopeRow} label="Tools" value=${tools} />
      <${ScopeRow} label="Bash" value=${scope.bash_allowed ? "✓ enabled" : "✗ disabled"} />
      <${ScopeRow} label="Email to" value=${emailTo} />
      <${ScopeRow} label="Paths" value=${paths} />
      <${ScopeRow} label="Sub-depth" value=${scope.max_depth + " level(s)"} />
    </div>
    ${!isRevoked
      ? html`<div class="tc-actions">
          <button class="btn deny tc-revoke-btn" onClick=${handleRevoke}>
            ✕ Revoke Token
          </button>
          <span class="tc-hint">Blocks all further tool calls this session</span>
        </div>`
      : html`<div class="tc-revoked-note">All tool calls from this session are now blocked at tier 0.</div>`}
    ${onVerifyAudit
      ? html`<div class="tc-audit">
          <button class="btn ghost tc-audit-btn" onClick=${verify}>Verify audit chain</button>
          ${auditState === "checking"
            ? html`<span class="tc-hint">checking…</span>`
            : auditState
              ? html`<span class=${"tc-audit-result " + (auditState.ok ? "ok" : "bad")}>
                  ${auditState.ok
                    ? `✓ intact · ${auditState.length} event${auditState.length === 1 ? "" : "s"} verified`
                    : `✗ ${auditState.reason || "chain broken"}`}
                </span>`
              : null}
        </div>`
      : null}
  </div>`;
}
