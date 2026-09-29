# Evaluation brief

## Problem and decision

A hypothetical customer needs to rotate a shared service credential without breaking applications that adopt it at different times. The decision is whether staged delivery with observed consumer verification is a suitable design for that customer's environment.

This lab validates controller behavior under declared assumptions. It does not establish that a particular vendor supports overlapping credentials or provides this orchestration automatically.

## Discovery before implementation

1. Which system issues and invalidates the credential? Can two versions be valid concurrently?
2. Which applications, jobs and long-lived connections use it? Who owns that inventory?
3. How do consumers obtain changes: restart, file reload, SDK refresh or a deployment?
4. What authenticated operation demonstrates that each consumer is using the replacement successfully?
5. How long may both versions remain valid, and who owns recovery if that limit is reached?
6. Which actions require approval, and which identity should have each read, issue and revoke permission?

If the issuer permits only one active credential, this overlap-and-rollback design is unsuitable without changing the rotation mechanism or accepting a maintenance window.

## Lab scope

- One synthetic credential path and two versions.
- Three explicitly registered consumers; one canary and two subsequent promotions.
- Controller can create and revoke; consumers can read their allowed path.
- A mock authenticated operation verifies adoption.
- Failure injection covers the canary, a subsequent consumer and rollback itself.
- Every run starts from a fresh in-memory environment. It is not a restartable workflow.

## Acceptance criteria

| Criterion | Evidence |
| --- | --- |
| All registered consumers adopt the candidate before retiring the old version | Happy scenario and revocation-time state test |
| Canary failure prevents promotion of other consumers | Canary failure scenario |
| Failure after canary promotion restores previously promoted consumers | Downstream failure scenario |
| Unverified rollback cannot retire either version | Rollback failure scenario |
| A reader cannot write or revoke; another path cannot reveal versions | Access and path-isolation tests |
| Missing candidate retrieval preserves existing configuration | Missing-secret test |
| Desired version changes alone are insufficient | Stale-observation revocation test |
| Reports contain no mock credential values | Determinism and output-disclosure test |

## Measures and interpretation

Report observed adoption as a count of verified consumers out of the declared inventory. The lab's successful result is **3 of 3 mock consumers**, not evidence about a real deployment.

Record which consumers were exposed to a failed candidate. The canary failure case exposes one mock consumer; the downstream failure case exposes two. This demonstrates sequencing, not a measured reduction in business incidents.

In a real pilot, measure authenticated failure rate, time to full adoption, duration of credential overlap, rollback completion time, inventory coverage and time spent in unknown states. Establish thresholds with the customer before testing. The lab has no clock or production traffic, so it reports no latency, uptime or savings claims.

## Next integration boundary

Replace these mock capabilities separately:

- Secret manager: version retrieval and identity-scoped permissions.
- Credential issuer: create, activate and invalidate the upstream credential.
- Deployment system: distribute the version reference and trigger reload or restart.
- Application probe: establish which credential version actually works for each consumer.
- Rollout ledger: persist observations, attempt identity and recovery state across crashes.

Revalidate behavior with issuer errors, stale reads, unknown consumers, network partitions and concurrent changes. Success in this lab is permission to investigate those integrations, not approval to deploy them.

## Provenance

AI-assisted implementation created from a hypothetical scenario. No employer source code, architecture exports, credentials, customer identifiers or measured production outcomes were used.
