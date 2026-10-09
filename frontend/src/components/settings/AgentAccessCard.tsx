// Agent access (MCP), adapted from the mockup's card (midataworks-mockup.jsx). Display only: the
// address, the tool categories and the approval rules are deployment configuration (P-08), so
// every field is read-only and says where it is set.
import { useEffect } from 'react';
import { Bot } from 'lucide-react';

import { useAgentAccessStore } from '@/stores/agentAccessStore';

export const ACTION_LABELS: Record<string, string> = {
  hub_push: 'Hub push',
  agent_label_rows: 'Agent labelling over the row limit',
  version_delete: 'Version delete',
  millm_model_load: 'miLLM model load',
  secret_write: 'Secret write (token or API key)',
  source_annotate: 'Source annotation',
  gate_target_write: 'Calibration target change',
};

const field =
  'w-full rounded-md border border-slate-300 bg-slate-50 px-2 py-1 font-mono text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300';

function ReadOnly({ id, label, value }: { id: string; label: string; value: string }) {
  return (
    <div className="mb-3">
      <label htmlFor={id} className="block text-xs font-medium mb-1 text-slate-700 dark:text-slate-300">
        {label}
      </label>
      <input id={id} className={field} value={value} readOnly aria-readonly="true" />
      <p className="text-xs mt-1 text-slate-500 dark:text-slate-400">Set in the deployment.</p>
    </div>
  );
}

export function AgentAccessCard() {
  const access = useAgentAccessStore((s) => s.access);
  const error = useAgentAccessStore((s) => s.error);
  const fetchAccess = useAgentAccessStore((s) => s.fetchAccess);

  useEffect(() => {
    void fetchAccess();
  }, [fetchAccess]);

  return (
    <section
      className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900"
      aria-labelledby="agent-title"
      data-testid="agent-access-card"
    >
      <h2 id="agent-title" className="font-semibold mb-1 text-slate-900 dark:text-slate-100">
        Agent access (MCP)
      </h2>
      <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
        Agents reach miDataworks through its MCP server with one bearer token. Some actions wait for your approval in
        the banner.
      </p>
      {error && (
        <p role="alert" className="text-xs mb-2 text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {access && (
        <>
          <ReadOnly id="mcp-url" label="MCP server address" value={access.mcp_public_url ?? 'Not configured'} />
          {access.mcp.reachable && access.mcp.categories ? (
            <ReadOnly id="mcp-categories" label="Tool categories" value={access.mcp.categories.join(', ')} />
          ) : (
            <p className="text-xs mb-3 text-amber-800 dark:text-amber-300" data-testid="mcp-unreachable">
              The MCP server cannot be read: {access.mcp.reason ?? 'no reason given'}. Check the midataworks-mcp
              deployment.
            </p>
          )}
          <h3 className="text-xs font-medium mb-1 text-slate-700 dark:text-slate-300">These actions wait for your approval</h3>
          <ul className="text-xs mb-2 list-disc pl-5 text-slate-700 dark:text-slate-300" data-testid="gated-actions">
            {Object.keys(access.gated_actions).map((action) => (
              <li key={action}>{ACTION_LABELS[action] ?? action}</li>
            ))}
          </ul>
          <p className="text-xs mb-3 text-slate-600 dark:text-slate-400" data-testid="label-rule">
            Agent labelling waits for approval above {access.label_threshold.toLocaleString('en-US')} rows per version
            per {access.label_window_hours} h. A request expires after {access.approval_ttl_hours} h. Set in the
            deployment.
          </p>
          <p className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400" data-testid="agent-activity">
            <Bot size={14} aria-hidden="true" />
            {access.activity.requests} request{access.activity.requests === 1 ? '' : 's'} from{' '}
            {access.activity.identities.length} agent identit{access.activity.identities.length === 1 ? 'y' : 'ies'} in{' '}
            {access.activity.sessions} session{access.activity.sessions === 1 ? '' : 's'}, in the last hour.
          </p>
        </>
      )}
    </section>
  );
}
