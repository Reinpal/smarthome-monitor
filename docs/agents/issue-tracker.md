# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `Reinpal/smarthome-monitor`.
Use the `gh` CLI from this clone.

## Operations

- Create/publish: `gh issue create --title "..." --body-file <file>`.
  Use a heredoc to prepare multi-line bodies.
- Read/fetch: `gh issue view <number> --comments`.
  Include labels when fetching structured issue data.
- List: `gh issue list --state open --json number,title,body,labels,comments`.
  Apply label and state filters as needed.
- Comment: `gh issue comment <number> --body "..."`.
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- Close: `gh issue close <number> --comment "..."`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub issues and PRs share a number space. When the type is uncertain,
try `gh pr view <number>` and fall back to `gh issue view <number>`.

## Wayfinding

- Keep the map in one issue labelled `wayfinder:map`.
- Link child tickets as GitHub sub-issues. If unavailable, use a task list
  in the map and `Part of #<map>` in each child.
- Label children `wayfinder:<type>`: research, prototype, grilling, or task.
- Record blockers using native GitHub issue dependencies via `gh api`.
  Dependency endpoints require the blocker's database ID, not its issue number.
  If unavailable, use `Blocked by: #<number>` in the child body.
- Select the first open, unassigned child in map order with no open blockers.
- Claim with `gh issue edit <number> --add-assignee @me` as the session's first write.
- Resolve by commenting with the answer, closing the child, and appending
  a summary and link to the map's Decisions-so-far.
