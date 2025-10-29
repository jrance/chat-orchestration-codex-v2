# AGENTS - Principles & Conventions (Seed)

- **Versioned APIs**: `/v1/*` only.
- **Configuration**: `.env` for local dev; overridable in tests.
- **Extensibility**: Node/Agent/Tool registries added in future PRs.
- **Performance**: Async everywhere; bounded concurrency to be introduced.
- **Testing**: Each PR maintains >=80% coverage for new code.

## Prompt Tokens

- Default tokens: `{tenant_id}`, `{correlation_id}`, `{request_id}`, `{now_iso}`, `{date_yyyy_mm_dd}`, `{user_details}` (JSON encoded).
- Unknown tokens render literally; register new tokens with `app.context.tokens.register_token`.

## Prompt Assembly Order

1. Organization preamble (when `context.injectOrgPreamble` is true and text is configured).
2. Agent style guide (`data.styleGuide`).
3. Expanded system instructions (`data.systemInstructions` with tokens).
4. Context variables (`data.context.vars` rendered as JSON).
