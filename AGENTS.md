# AGENTS - Principles & Conventions (Seed)

- **Versioned APIs**: `/v1/*` only.
- **Configuration**: `.env` for local dev; overridable in tests.
- **Extensibility**: Node/Agent/Tool registries added in future PRs.
- **Performance**: Async everywhere; bounded concurrency to be introduced.
- **Testing**: Each PR maintains >=80% coverage for new code.
