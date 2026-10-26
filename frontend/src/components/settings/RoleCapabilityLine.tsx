// "Test endpoint" and its result (FR-005.9): reachable, model listed, protocol, whether the server is
// miLLM (in cyan, R-03.59) with its loaded model and lease state. Distinct codes for unreachable,
// 401, non-JSON and protocol unsupported.
import { Button } from '@/components/common/Button';
import { useLabelingStore } from '@/stores/labelingStore';

export function RoleCapabilityLine({ role }: { role: string }) {
  const result = useLabelingStore((s) => s.tests[role]);
  const testEndpoint = useLabelingStore((s) => s.testEndpoint);
  const ok = result && result.reachable && result.protocol_ok && !result.error_code;
  return (
    <div className="mb-4" data-testid={`capability-${role}`}>
      <Button size="sm" variant="secondary" onClick={() => void testEndpoint(role)}>Test endpoint</Button>
      {result && (
        <p className={`text-xs mt-2 ${ok ? 'text-green-700 dark:text-green-400' : 'text-amber-700 dark:text-amber-400'}`} role="status">
          {result.reachable ? 'Reachable' : 'Unreachable'}
          {result.model_listed !== null && (result.model_listed ? ' · model listed' : ' · model not listed')}
          {result.protocol_ok !== null && (result.protocol_ok ? ' · protocol works' : ' · protocol not supported')}
          {result.server_kind === 'millm' && (
            <span className="text-cyan-700 dark:text-cyan-400"> · miLLM, {result.resident_model ?? 'no model'} loaded, lease {result.lease_state}</span>
          )}
          {result.server_kind && result.server_kind !== 'millm' && ` · ${result.server_kind === 'tei' ? 'TEI' : 'OpenAI-compatible server'}`}
          {result.error_code && ` · ${result.error_code}: ${result.message}`}
        </p>
      )}
    </div>
  );
}
