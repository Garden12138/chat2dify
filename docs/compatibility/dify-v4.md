# Builder Agent Dify compatibility matrix

Matrix version: `2026-07-26`

| Dify version | App DSL | Modes | Read/diagnostic | Workspace mutation/Commit | Evidence |
| --- | --- | --- | --- | --- | --- |
| `1.14.x` (tested `1.14.2`) | `0.6.0` | Workflow, Chatflow, Chatbot, Completion, Agent | Supported | Supported | Deterministic suite, local Dify Phase 0–2 acceptance, version fixtures |
| Other or unknown | Any unmatched version | All | Bounded diagnostics only | Fails closed | Unsupported-version fixture |

`test` / `9.9.9` is a repository-only deterministic fixture rule. It is never
reported as a production-supported Dify version.

The matrix is evaluated at Snapshot capture and pinned to the Run. Capability
records carry the same decision. A missing rule does not prevent bounded
inspection and validation, but `workflow.patch`, `config.patch`, and Commit
return `DIFY_VERSION_MUTATION_UNSUPPORTED`.

## Known Dify 1.14.2 boundary

The Workflow and Chatflow Draft Run Console endpoints accept test inputs but
not a candidate Graph or DSL. Therefore the built-in adapter:

- runs only an unchanged persisted baseline;
- returns `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED` after Workspace mutation;
- never temporarily syncs a target draft;
- never imports and deletes a hidden test app;
- requires an approved Commit followed by a new explicit Run for real
  post-repair verification.

Configured-app v4 modification uses the Console model-config endpoint after an
immediate model-config Hash/fingerprint comparison. New Chatbot, Completion,
and Agent creation remains on v3 in v4.0.0.

Compatibility fixtures live under
`app/evals/fixtures/compatibility/`. Add a fixture and deterministic adapter,
compiler, validation, Hash, and negative-path tests before expanding a rule.
