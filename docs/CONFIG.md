# Configuration

## Environment Files

- Precedence: process environment variables override `.env.local`, which overrides `.env`.
- Track shared defaults in `.env`; keep machine or secret values in `.env.local` (ignored by git).
- Ensure your editor/terminal runs commands from the repository root so dotenv files are discovered.
- At runtime call `settings.debug_summary()` to see which dotenv files were loaded and which Apigee keys were populated (no secret values are revealed).

## Secrets

- Store sensitive values such as `APIGEE_CLIENT_SECRET` in `.env.local`.
- Use production secret managers for deployed environments; dotenv files are only for local workflows.

## VS Code & Pylance

- Pylance must use the project's virtual environment to resolve dependencies such as `pydantic_settings`.
- Select the interpreter located at `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS/Linux via **Python: Select Interpreter**.
