---
name: system
description: Safe execution of computer and operating-system actions.
task_types: [SYSTEM_ACTION]
triggers: [open, close, launch, run, execute, install, create, delete, move, rename, setup, configure, change]
tools: [terminal, filesystem]
risk: high
requires_verification: true
---

# System Skill

## Purpose
Perform computer actions safely and predictably.

## Workflow
1. Determine exactly what the user asked to change.
2. Inspect the target before modifying it when practical.
3. Classify the action by risk.
4. Request approval for sensitive or destructive actions.
5. Execute through the approved tool path.
6. Capture the result.
7. Verify the system state after execution.
8. Report failures honestly.

## Rules
- Deny unsafe or unauthorized operations.
- Never bypass the approval system.
- Prefer reversible operations.
- Never claim an action succeeded without checking its result.