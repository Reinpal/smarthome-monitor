# Domain docs

## Layout and reading rules

This repo uses a single-context layout:
- `CONTEXT.md` at the repo root holds domain terminology.
- `docs/adr/` holds architectural decisions.

Before exploring the codebase, read `CONTEXT.md` and ADRs relevant to the area.

If these files do not exist, proceed silently without suggesting their
creation upfront. The domain-modeling skill creates them lazily as terms
and decisions are resolved.

## Vocabulary

Use the glossary's terms when naming domain concepts in issues, proposals,
hypotheses, and tests. If a concept is missing, reconsider the terminology
or note the gap for domain-modeling.

## ADR conflicts

Explicitly flag proposals that contradict an existing ADR, naming the ADR
and explaining why the decision is worth reopening.
