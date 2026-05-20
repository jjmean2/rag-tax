# Project Guidelines

## Conventions

- When the user asks for a commit message, a commit plan, or asks to commit changes, use the Conventional Commits specification.
- Format commit subjects as `<type>(<optional scope>): <description>`.
- Prefer these commit types unless the user requests otherwise: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`.
- Keep the subject line concise and imperative.
- Use a scope when it clarifies the affected area, such as `api`, `ui`, `search`, `ingestion`, or `docs`.
- If a change is breaking, include `!` after the type or scope and clearly mention the breaking change in the body.
- When suggesting commit messages, reflect the actual set of changes instead of generic summaries.
- If changes mix unrelated concerns, prefer recommending separate commits.

## Build and Test

- Use `uv` for Python dependency management and execution.
- Run Python commands with `uv run ...` when the project depends on installed packages.
