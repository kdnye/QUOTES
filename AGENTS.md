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

## Client Integration Standards

These rules apply when writing or reviewing code for external API clients (Excel Power Query, Google Sheets Apps Script, Python scripts, etc.).

### String concatenation
- **M (Power Query / Excel)**: always use `&` for string joining. `+` is arithmetic only and will raise a type error on text values.
- **Apps Script (Google Sheets)**: always use `+` for string joining.

### Parameter naming (Power Query)
Never name a function parameter the same as a JSON key used in the same scope. M silently shadows the parameter with the key binding, producing an "unbound name" compile error that is hard to diagnose. Prefix function parameters (e.g., `pOrigin`, `pDestination`, `pWeight`) to avoid collisions.

### Data types
- Always send ZIP codes as 5-character strings. Wrap inputs in `Text.From()` (M) or `str(zip).zfill(5)` (Python) to prevent leading-zero stripping.
- Use `Text.Proper()` (M) or `.title()` (Python) on `quote_type` to normalize user input (`"hotshot"` → `"Hotshot"`).

### Error handling
- Use `ManualStatusHandling = {400, 403, 404, 429, 500}` in Power Query `Web.Contents` options so non-2xx responses return a parseable body instead of an exception.
- Always surface the `remediation` field from API error responses — it contains the actionable next step for the user, not just an error code.
- The `Authorization` header must be exactly `Bearer <token>` with a single space after `Bearer`. A missing space returns `401 Unauthorized`.

### Authentication
- Per-user API keys are issued via the admin dashboard and scoped to a single user. Quotes made with a per-user key are automatically attributed to that user in the database.
- The global service token (`API_AUTH_TOKEN`) is for server-to-server integrations. Quotes made with the global token have no user attribution and are tagged `quote_source="api_service"` in the database.
