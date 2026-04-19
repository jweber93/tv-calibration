# Agent Instructions — tv-calibration

## Repo Context
- Stack: Python, ArgyllCMS, dogegen, Docker
- Hardware: Hisense U8G TV, Calibrite colorimeter, Dogegen pattern generator
- Purpose: Automated TV display calibration

## Standard Commands

When I say **"resolve issue [URL]"**, **"fix bug"**, **"debug"**, **"work on"**, or describe any bug/defect, execute the Bug Resolution Protocol:
- Read full relevant codebase context before writing any code
- Identify root cause, not symptom
- Reuse existing patterns and abstractions
- Flag scope creep before proceeding
- Write unit + integration tests, all must pass
- Atomic commits, imperative mood, <72 chars, no "fix"/"update"/"misc"
- At least one commit message must reference the issue: `(#[issue-number])`
- Branch: `fix/[short-slug]`
- Push and **open a draft PR** — always, without being asked
- PR body **must** include `Closes #[issue-number]` so GitHub auto-closes the issue on merge
- If the issue URL was given, extract the number from it; if only a description was given, search for the matching open issue first

When I say **"implement feature"**, execute the Feature Implementation Protocol:
- Read AGENTS.md and CONTRIBUTING.md first
- Write a 5-bullet plan with assumptions before touching code
- Flag hidden complexity before proceeding
- Production quality, full test coverage, docs updated
- Follow commit/branch/PR conventions above
- Push and **open a draft PR** — always, without being asked
- If this work originated from an issue, include `Closes #[issue-number]` in the PR body

When I say **"audit codebase"**, execute the Codebase Audit Protocol:
- Act as Principal Engineer + Color Scientist
- Identify bugs, math errors in color processing, hardware comm bottlenecks
- For each issue: create formal GitHub issue with title, description, labels
- Labels: bug, high-priority, math-error, hardware-io
- Include fix strategy with root cause + implementation plan
- Format output suitable for direct AI agent execution

When I say **"audit QE"**, execute the QE Audit Protocol:
- Act as Staff Platform Engineer + QE Architect
- Design hardware mocking strategy for headless CI
- Produce: ci.yml template, pytest structure with fixtures, prioritized roadmap

## Git Workflow
1. `git checkout -b [feat|fix|chore]/[short-slug]`
2. Atomic commits, imperative mood, <72 chars, no "fix"/"update"/"misc"
3. `git push -u origin HEAD`
4. **Open a draft PR immediately after first push** — do not wait to be asked

## Post-Merge Cleanup

When I say **"cleanup"** or **"branch was merged"** or **"PR [number] merged"**, execute the Cleanup Protocol:
- `git fetch -p` to prune remote tracking refs
- `git checkout main && git pull origin main`
- `git branch -d [merged-branch]` (local cleanup)
- Check for any stale fix/* or feat/* branches older than 7 days and list them
- Report what was cleaned

> Note: Auto-delete on GitHub is handled by the `.github/workflows/cleanup.yml` action — you only need the local steps above.

## Non-negotiables
- Tests must pass. Never leave broken tests.
- No mocking things that don't need mocking.
- If blocked or ambiguous, stop and report — do not work around it.
- No TODOs or placeholders. Production quality only.
- **Every task that touches code ends with a pushed branch and an open PR.** No exceptions.

## Memory Protocol

**At the start of every session:**
- Call `opencode_mem_search_memory` with the current task description
- Review results and use them to inform your approach

**During the session, save to memory when you:**
- Make an architectural or design decision
- Discover a non-obvious bug or root cause
- Establish a pattern or convention for this codebase
- Complete a significant piece of work

**At the end of every session:**
- Save a summary of what was done and any decisions made
- Save any context the next session will need

**Memory entries should be concise and specific** — not "worked on calibration" 
but "decided to use CV 738 as clip point because U8G clips around CV 840-895".
