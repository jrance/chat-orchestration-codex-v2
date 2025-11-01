# Configuration

## Environment Files

- Precedence: process environment variables override `.env.local`, which overrides `.env`.
- Track shared defaults in `.env`; keep machine or secret values in `.env.local` (ignored by git).
- Ensure your editor/terminal runs commands from the repository root so dotenv files are discovered.

## Secrets

- Store sensitive values such as `APIGEE_CLIENT_SECRET` in `.env.local`.
- Use production secret managers for deployed environments; dotenv files are only for local workflows.
