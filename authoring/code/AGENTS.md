# AGENTS.md

## Purpose
Use this folder when the task is to select and apply a shared source-code authoring guide.

## Code Guide Selection

When choosing among code-authoring guides in this folder:

- identify the primary language
- identify the primary task mode, such as writing new code, editing existing code, reviewing code, or refactoring code
- for Python source files or Python-focused code review, prefer `authoring/code/python.md`
- check repo-local instructions and existing code conventions first
- prefer the most specific guide that matches the language and task mode

When the right guide is still not clear after this comparison:

- ask the user directly which guide should govern the work before making substantial edits

## Use Of Shared Code Guides

- Use shared code-authoring guides for reusable editing defaults, not for repo-specific architecture or workflow rules.
- More specific repo or subtree `AGENTS.md` files override this folder's shared defaults within their scope.
- Follow repo-local `AGENTS.md` files when they define architecture, contracts, deployment rules, test commands, or other local conventions.
- When the task is to revise a coding prompt asset rather than source code, use `authoring/agents/coding-prompt.md` instead of this folder.

## Practical Rule

Use this folder to answer:

- which shared code-authoring guide applies
