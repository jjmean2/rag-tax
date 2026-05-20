---
name: Conventional Commit
description: "Use when: generating a Conventional Commits style commit message for the current workspace changes"
argument-hint: "Describe the change set or ask to inspect current workspace changes"
agent: agent
---

Generate a commit message that follows the Conventional Commits specification.

Requirements:

- Inspect the current workspace changes before writing the message unless the user already provided an exact change summary.
- Use the format `<type>(<optional scope>): <description>`.
- Choose the narrowest accurate type from `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, or `chore`.
- Add a scope when it improves clarity.
- Keep the subject concise and imperative.
- If the changes contain multiple unrelated concerns, propose a small set of separate commit messages instead of collapsing them into one.
- If there is a breaking change, mark it using Conventional Commits rules and include a short body.

Output format:

- First line: recommended commit message
- Then: one short explanation of why that type and scope were chosen
