# Agent Instructions — tv-calibration

## Repo Context
- Stack: Python, ArgyllCMS, dogegen, Docker
- Hardware: Hisense U8G TV, Calibrite colorimeter, Dogegen pattern generator
- Purpose: Automated TV display calibration

## Standard Commands

When I say **"resolve issue [URL]"**, execute the Issue Resolution Protocol:
- Read full relevant codebase context before writing any code
- Identify root cause, not symptom
- Reuse existing patterns and abstractions
- Flag scope creep before proceeding
- Write unit + integration tests, all must pass
- Atomic commits, imperative mood, <72 chars, no "fix"/"update"/"misc"
- Branch: [feat|fix|chore]/[short-slug]
- Push and create PR when done

When I say **"implement feature"**, execute the Feature Implementation Protocol:
- Read AGENTS.md and CONTRIBUTING.md first
- Write a 5-bullet plan with assumptions before touching code
- Flag hidden complexity before proceeding
- Production quality, full test coverage, docs updated
- Follow commit/branch/PR conventions above

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
4. Open PR

## Non-negotiables
- Tests must pass. Never leave broken tests.
- No mocking things that don't need mocking.
- If blocked or ambiguous, stop and report — do not work around it.
- No TODOs or placeholders. Production quality only.

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
