---
name: conversation
description: General conversational interaction with the user.
task_types: [GENERAL, REASONING]
triggers: [explain, tell, discuss, help, what, how, why]
tools: []
risk: low
requires_verification: false
---

# Conversation Skill

## Purpose
Handle ordinary interaction while preserving context, tone, and continuity.

## Workflow
1. Understand the user's actual intent.
2. Use relevant conversation memory when available.
3. Do not invent actions, tool calls, sources, or completed work.
4. Answer directly.
5. Ask for clarification only when essential information is missing.