# Commercial redesign audit

## Scope

The audit covered the commercial homepage, operations overview, seeded demo
integrity, reset boundary, desktop and mobile behavior, and automated quality
gates.

## Findings and resolution

| Finding                                                                                                  | Evidence                                                                                    | Resolution                                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The former homepage framed TradeFlow as a portfolio artifact.                                            | Primary copy and README used reviewer and portfolio language.                               | Replaced with a commercial product narrative, outcome-led sections, real product captures, and operational trust evidence.                                                  |
| `/demo` presented connection diagnostics instead of business work.                                       | The dominant surface was a platform handoff/control desk.                                   | Replaced it with a server-authoritative operations overview and a prioritized action queue.                                                                                 |
| Seeded stages skipped authoritative submission and pick-release transitions.                             | Seed setup wrote later states without demonstrating each accepted lifecycle boundary.       | Added order submission, explicit approval, pick release, partial pick, dispatch, delivery confirmation, invoice posting, payment clearing, and allocation.                  |
| Reset readiness did not prove the complete demo contract.                                                | A successful process could publish ready without checking every required operational state. | Added post-seed database requirements, fail-closed readiness, an authenticated reset header, and API maintenance responses.                                                 |
| Deployed credentials could be read before their atomic replacement and remain failed in the web process. | Reproduced against the container stack during reset.                                        | Limited recovery to missing-file errors and verified mode `0400`, container UID ownership, reset-time HTTP 503, and post-reset recovery without restarting the web service. |
| Small secondary marketing text missed AA contrast.                                                       | Axe reported contrast failures on the first accessibility run.                              | Darkened the secondary ink token and reran WCAG A/AA checks on desktop and mobile.                                                                                          |
| Retired control-desk assertions remained in browser tests.                                               | The legacy platform-shell suite asserted the old heading.                                   | Removed redundant mocked coverage and updated real-stack coverage to the operations overview contract.                                                                      |

## Accepted design system

- Personality: precise, dependable, composed enterprise operations.
- Signature element: an accountable-flow ledger with a dominant action queue.
- Palette: cool paper, blue-gray steel, navy ink, restrained cobalt, semantic colors.
- Density: moderate on marketing surfaces and high but touch-safe in operations.
- Motion: limited to state feedback and disabled when reduced motion is requested.
- Rejected patterns: portfolio framing, decorative charts, generic metric-card grids,
  excessive rounding, glass effects, gradients, and color-only status cues.
