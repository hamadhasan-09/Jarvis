---
name: knowledge
description: Retrieval and synthesis from JARVIS memory, documents, notes, and indexed knowledge.
task_types: [KNOWLEDGE_QA, REASONING]
triggers: [document, documents, pdf, notes, knowledge, knowledgebase, manual, documentation, docs, files, memory]
tools: [memory, retrieval]
risk: low
requires_verification: false
---

# Knowledge Skill

## Purpose
Answer from the user's available knowledge sources while keeping retrieved evidence separate from model assumptions.

## Workflow
1. Identify which knowledge source is relevant.
2. Retrieve only the material needed for the task.
3. Prefer exact source evidence over inferred information.
4. Combine retrieved information with conversation context.
5. Clearly distinguish retrieved facts from general reasoning.
6. State when the required information is unavailable.