# AGENTS Instructions

These instructions apply to the entire repository.

## Code style
- Use Python 3.8+.
- Format code with `black` using the default line length (88).
- Indent with 4 spaces.
- Include type hints for new or modified functions.
- Keep commits focused and avoid unrelated changes.

## Testing
- Write or update tests for any code changes.
- Run the full test suite with `pytest` before committing.

## Documentation
- Update README or docstrings when behavior changes.

## Docstrings and Comments
- Write clear, beginner-friendly docstrings and comments.
- For every new or modified function or class, explain its inputs, outputs, and any external dependencies.
- Link to the source of external calls to help newcomers find definitions (e.g., "calls `services.auth_utils.is_valid_email`").

## Pull Requests
- In the PR description, summarize changes and how they were tested.
- Mention any new dependencies or migration steps.
