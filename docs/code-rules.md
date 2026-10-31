# Code rules (feature 018)

A code rule is a short Python function that counts something in a text, for the facts a rule set
cannot express with patterns, validators and proximity. A rule set's `code` signal names a code
rule by its hash; a build runs it on every row, and the count becomes a signal like any other.

Agents write code rules through their tools (MCP) and the REST routes. The operator decides, on
the **Approvals** screen and the **Code rules** screen, which exact code may run.

## What a code rule is

A code rule is a document with:

- **source**: Python, run by CPython inside WebAssembly. Only these standard modules may be
  imported: `bisect`, `collections`, `decimal`, `fractions`, `functools`, `heapq`, `itertools`,
  `json`, `math`, `operator`, `re`, `statistics`, `string`, `unicodedata`. Anything else is
  refused when the code loads (`rule_load_refused`).
- **functions**: the names the source exports; each takes the text and the parameters and returns
  a count.
- **params_schema**: the JSON Schema the parameters of a call must satisfy.
- **runner_contract**: the contract version the rule was written against.
- **cases**: texts with their expected answer (a count, or a reason). Every function needs at
  least one case expecting a count of one or more and one expecting zero. Cases are stored beside
  the document but are not part of its hash, so editing a case needs no new approval.

The hash (SHA-256 of the canonical document) is the code rule's identity. Any byte changed in the
source, the functions, the schema, the derivation or the contract is a new hash.

## A code rule can only report a count

The runner answers each call with one line that the backend reads with a strict grammar. The only
answer that becomes a value is a non-negative whole number (clamped at 1,000); `True`, a float, a
string or anything else is `rule_output_invalid`. A code rule therefore cannot carry text out of a
row: it can only say how many.

Error details (the exception type, line and message) are kept only for a dry run, and only for the
agent's own test text. A build records the reason and never the details, so a row's text is never
echoed back through an error.

## Approval and revocation

- **Saving.** An agent's save of a hash that is not approved waits on the Approvals screen. Every
  check runs before the request is queued: the static checks, then every case, twice, on the
  runner. The request's card shows the hash's standing, read when the card opens (never approved, or
  revoked by you, when and why; approving a request whose hash was revoked meanwhile is refused), the full source, the change against the previous version, the functions, the
  parameter schema, every case result, the imports and the full hash. Approving it approves that
  exact code. The operator's own save approves it directly.
- **Revoke.** On the Code rules screen, Revoke stops an approved hash from running in rule set
  previews and builds from now on (a dry run still runs any code, approved or not, on the agent's
  own test texts, and stores nothing). Versions already built with it keep their record. It takes a reason.
- **Approve again.** A revoked hash comes back only through Approve again on the Code rules
  screen, with a reason. Its stored cases run again under the current contract first: a failing
  case answers `code_rule_case_failed` naming the case, and an unreachable runner answers
  `runner_unavailable`; in both cases nothing changes. Saving a revoked hash again is refused
  (`code_rule_revoked`), for an agent and the operator alike: changing the code makes a new hash.
- Revoke and Approve again are the operator's alone: an agent calling them is refused
  (`agent_forbidden`). Both ask for the Settings personal identification number (PIN) when one is
  set.

Every decision is kept in the hash's approval history with who made it, when and why.

## The runner

Code rules run in a separate service, the runner, never in the backend or a worker. The runner has
no database, no secrets and no network access of its own; each call runs in a fresh WebAssembly
instance with fuel, memory, input and output limits, and the same code on the same text always
gives the same answer. The limits in force, the allowed modules and the reasons are published by
`GET /api/v1/runner/contract`.

The backend does not need the runner to start. With the runner down, `GET /api/health` reports
`runner` as not reachable while the app stays up; dry runs and saves answer `503
runner_unavailable`, and build rows read `rule_worker_unavailable`. Code rules can be switched off
for a deployment (`CODE_RULES_ENABLED`); they then answer `code_rules_disabled`.

When the runner's contract changes, every stored code rule's cases run again under the new
contract. A code rule failing under the current contract is shown as failing on the Code rules
screen, and new builds refuse it.

## Failure reasons

| Reason | Meaning |
|---|---|
| `rule_error` | the code raised, did not compile, or did not export the function |
| `rule_load_refused` | the code imported a module that is not allowed or asked for a refused capability |
| `rule_resource_limit` | the code ran out of fuel or memory, or recursed too deep |
| `rule_output_invalid` | the function returned anything but a non-negative whole number |
| `rule_input_too_large` | the text is longer than the input limit in force |
| `rule_timeout` | the wall-time backstop expired (a host fault, never a verdict on the text) |
| `rule_worker_unavailable` | the runner could not be reached or stayed busy (a host fault) |
| `rule_not_approved` | the code's hash is not approved in this deployment |

A build counts the rows a code rule failed on against its failure share (`CODE_RULE_FAILURE_SHARE`);
above it the step fails (`code_rule_failure_share_exceeded`) rather than dropping rows quietly.
