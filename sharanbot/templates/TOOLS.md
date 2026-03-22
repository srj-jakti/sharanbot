# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s, max 600s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## exec — Error Recovery Rules

- **Permission errors**: If `pip install` fails with "permission denied", retry with `pip install --user <package>` or create a virtual environment (`python -m venv venv`) first
- **Port conflicts**: If a port is occupied, do NOT kill processes and retry the same port repeatedly. Instead, choose a different port or use the `--port` flag
- **Stuck loops**: If you've tried the same command pattern 3+ times without success, STOP and fundamentally rethink the approach. Ask yourself: "Is there a completely different way to achieve this?"
- **Interactive prompts**: Some commands (expo, npm) prompt for input interactively. Since exec is non-interactive, these will hang or fail. Use flags like `--yes`, `--no-interactive`, or pipe `echo y |` to handle them
- **Long-running servers**: For dev servers (expo, vite, uvicorn), use timeout=120 or higher. Check if the server started by looking for success messages in output

## browser — Verification

- After launching any web app or server, use `browser` with action `navigate` to open the URL, then `screenshot` to capture and verify the page loaded correctly
- Check for error messages, broken layouts, or loading spinners in the screenshot
- If there are console errors visible, diagnose and fix them before reporting success

## cron — Scheduled Reminders

- Please refer to cron skill for usage.
