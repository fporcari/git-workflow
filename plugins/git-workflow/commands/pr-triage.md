---
description: Show every open PR you are involved in, split by the work it needs, then hand the actionable set to pr-loop. Explicit invocation only.
argument-hint: optional — a scope note; the triage itself takes no arguments
allowed-tools: Bash, Read, Grep, Glob, Agent, AskUserQuestion, ToolSearch, mcp__ccd_session_mgmt__set_session_title
---

Read `${CLAUDE_PLUGIN_ROOT}/skills/pr-triage/SKILL.md` completely and follow
it with `$ARGUMENTS` as the invocation text.
