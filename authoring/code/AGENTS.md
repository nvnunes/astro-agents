# AGENTS.md

## Purpose
Use this folder when the task is to choose and apply a shared source-code authoring guide.

## Code Guide Selection

When choosing among code-authoring guides in this folder:

- identify the primary language
- identify the primary task mode, such as writing new code, editing existing code, reviewing code, or refactoring code
- for Python source files or Python-focused code review, prefer `authoring/code/python.md`
- check project-local instructions and existing code conventions first
- prefer the most specific guide that matches the language and task mode

When the right guide is still not clear after this comparison:

- ask the user directly which guide should apply to the work before making substantial edits

## Use Of Shared Code Guides

- Use shared code-authoring guides for reusable editing defaults, not for project-specific architecture or workflow rules.
- Follow project-local `AGENTS.md` files when they define architecture, contracts, deployment rules, test commands, or other local conventions.
- When the task is to revise a coding prompt rather than source code, use `authoring/agents/coding-prompt.md` instead of this folder.

## Practical Rule

Use this folder to answer:

- which shared code-authoring guide applies
