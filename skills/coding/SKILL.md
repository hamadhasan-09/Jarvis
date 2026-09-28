---
name: coding
description: Software engineering, debugging, implementation, testing, and code review.
task_types: [CODING, REASONING]
triggers: [code, coding, python, javascript, c++, bug, debug, debugging, function, script, program, api, repository, git]
tools: [terminal, python]
risk: medium
requires_verification: true
---

# Coding Skill

## Purpose
Solve software-engineering tasks using an inspect -> implement -> test -> verify workflow.

## Workflow
1. Inspect the relevant files before changing code.
2. Understand the existing architecture.
3. Prefer the smallest safe change that satisfies the requirement.
4. Preserve existing working behavior unless replacement is intentional.
5. Implement complete production code rather than placeholder/demo code.
6. Run syntax checks and relevant tests.
7. Inspect failures before attempting a second implementation.
8. Verify the final behavior against the original requirement.
9. Report exactly what changed and what was actually tested.

## Rules
- Never claim code was tested when it was not.
- Never silently delete existing functionality.
- Avoid unnecessary dependencies.
- Do not overwrite unrelated user work.
- Treat generated code as untrusted until tested.