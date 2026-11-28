// Feature 018: operator code rules, as the backend returns them (services/code_rules/views.py,
// save.approval_facts). Every field the UI does not need is left out; nothing here is a source of
// truth.

export type CodeRuleApprovalState = 'approved' | 'revoked' | 'unknown';

/** `{count: n}` or `{reason: "..."}` (a case expects one; a result carries one). */
export interface CaseOutcome {
  count?: number;
  reason?: string;
}

export interface CaseResult {
  id: string;
  function: string;
  expected: CaseOutcome;
  result: CaseOutcome & Record<string, unknown>;
  passed: boolean;
}

export interface ApprovalDecision {
  id: number;
  action: 'approve' | 'revoke';
  reason: string;
  changed_by: string;
  approval_id: string | null;
  created_at: string | null;
}

/** `payload.facts` of a `code_rule_save` approval (save.approval_facts). The source itself is in
 *  `payload.body.document.source`, the document the agent sent. */
export interface CodeRuleFacts {
  name?: string;
  behaviour_key?: string;
  code_sha256?: string;
  approval_state?: CodeRuleApprovalState;
  last_decision?: ApprovalDecision | null;
  imports?: string[];
  functions?: string[];
  params_schema?: unknown;
  case_results?: CaseResult[];
  contract_version?: string;
  previous?: { version: number; code_sha256: string } | null;
  diff?: string;
}

export interface ContractStatus {
  contract_version: string;
  bundle_digest: string;
  passed: boolean;
  failing_case: string | null;
  reason: string | null;
  run_at: string | null;
}

export interface CodeRuleSummary {
  name: string;
  behaviour_key: string;
  version: number | null;
  code_sha256: string | null;
  approval_state: CodeRuleApprovalState;
  functions: string[];
  last_case_result: ContractStatus | null;
  archived: boolean;
}

export interface CodeRuleCase {
  id: string;
  function: string;
  text?: string;
  params?: unknown;
  expect: CaseOutcome;
}

export interface CodeRuleDocument {
  source: string;
  functions: Array<{ name: string } & Record<string, unknown>>;
  params_schema?: unknown;
  runner_contract?: string;
  derived_from?: unknown;
}

export interface CodeRuleUse {
  version_id: string;
  dataset_id: string;
  number: number;
  step_index: number;
  rule_set: string;
  failed_rows: number;
}

export interface CodeRuleDetail {
  name: string;
  version: number;
  behaviour_key: string;
  code_sha256: string;
  runner_contract: string;
  document: CodeRuleDocument;
  cases: CodeRuleCase[];
  approval: {
    state: CodeRuleApprovalState;
    pending_approvals: string[];
    history: ApprovalDecision[];
    history_rows: number;
  };
  contract_status: ContractStatus | null;
  /** The next version's number when a later one exists. */
  superseded_by: number | null;
  archived: boolean;
  created_by: string;
  created_by_origin: string;
  created_at: string | null;
  used_by: { recorded: boolean; versions: CodeRuleUse[] };
}

export interface ApprovalActionResult {
  code_sha256: string;
  state: CodeRuleApprovalState;
  changed: boolean;
  name: string;
  version: number;
  case_results?: CaseResult[];
}
