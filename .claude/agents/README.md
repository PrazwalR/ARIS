# ARIS audit agents

Twelve specialized audit subagents, one per domain, derived from the project's
"Principal Engineer Audit Mode" methodology. Invoke via the Agent tool with
`subagent_type` set to the agent's name, scoped to a specific path — these are
not meant to be run blind against the whole repo in one shot; give each a
concrete target and, where useful, the specific concern that prompted the audit.

| Agent | Domain |
| --- | --- |
| `hardcoded-value-detector` | Config values that should not be literals |
| `stub-simulation-hunter` | Fake implementations, mocks left in real code paths |
| `dead-code-eliminator` | Unused/duplicate/abandoned code |
| `security-auditor` | Secrets, injection, crypto, auth, input validation |
| `architecture-reviewer` | Structure, dependency graph, coupling/cohesion |
| `code-quality-inspector` | Naming, readability, code smells |
| `performance-auditor` | Hot-path allocations, complexity, blocking I/O |
| `error-handling-auditor` | Silent failures, missing cleanup/rollback |
| `dependency-auditor` | Unused/duplicate/oversized dependencies |
| `test-coverage-auditor` | Real coverage gaps (mutation-testing mindset, not %) |
| `bug-bounty-hunter` | Adversarial correctness/security review |
| `production-readiness-auditor` | CI/CD, config, deployment safety |

Each agent both finds and fixes issues in its target scope, then verifies with
the project's own lint/type-check/test commands. For a full multi-domain audit
of one module, launch several of these in parallel (single message, multiple
Agent tool calls) with the same target path, then synthesize their findings
before making further changes yourself.
