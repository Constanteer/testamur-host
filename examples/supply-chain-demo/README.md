# Deterministic supply-chain demo

This fixture gives the CLI, MCP tools, hosted app, screenshots, and launch walkthroughs one small repository-shaped input whose second observation has a stable mechanical delta.

## Observations

`baseline/requirements.txt` records:

- `requests==2.32.4`
- `flask==3.0.3`

`current/requirements.txt` records:

- `requests==2.32.5`
- no Flask declaration

A scanner/diff surface may therefore report a mechanically comparable requests version transition and a removed Flask declaration. Those observations are deliberately boring and deterministic so the product flow, not dependency trivia, is the demo.

## Expected journey

1. Create or select one Project.
2. Bind/scan `baseline/` and keep the resulting scan as the baseline observation.
3. Scan `current/` as a second immutable observation.
4. Open Compare and inspect the dependency rows.
5. Use the scoped `Review impact` handoff for a changed dependency.
6. Record any actual reliance/affectedness conclusion explicitly, then revalidate if appropriate.

The fixture is suitable for `testamur.project_scan`, `testamur.project_supply_chain`, and `testamur.project_supply_chain_diff` as well as the equivalent CLI/Web flow. Consumers should use the repository-local paths rather than replacing the fixture with network-fetched package metadata; the point is a repeatable first-run path.

## Semantic boundary

The fixture encodes observations, not verdicts. In particular:

- recorded != verified
- fetched != relied
- changed != invalid
- stale != false
- EXPOSED_TO_MODEL != RELIED
- `upgraded`, `downgraded`, `removed`, and `version-changed` are mechanical descriptions, not safety, validity, vulnerability, or affectedness conclusions
- no generic trust score is implied or expected

An identical rescan is still a new observation. A changed dependency row is a reason to review relevant impact, not evidence that impact exists.
