# Security and Access Control

## Trust boundary

Users authenticate to Redmine. Browser requests go to the plugin. The plugin derives access from current project permissions and sends an authenticated server-to-server request to RAG using a Bearer token and trusted access header. The browser must never receive either value.

Retrieval endpoint: `POST /api/v1/retrieve`.
Feedback endpoint: `POST /api/v1/feedback`; trusted authentication is required for all writes.

## Permissions

- `view_redmine_assistant`: use the project Assistant.
- `view_internal_assistant_documentation`: permit internal documentation in a project context.

Do not accept `access_context` or `include_internal` from browser parameters. Authorization occurs before retrieval; an LLM must never receive unauthorized content and then be asked to hide it.

## Defense in depth

1. Redmine derives the current access context.
2. Trusted headers require the shared service token.
3. Standard SQL retrieval excludes internal chunks.
4. The API defensively filters results.
5. The plugin defensively discards unexpected internal paths.
6. Automated tests and evaluations check leakage.
7. Feedback validates that the original retrieval context matches the current trusted context.

## Verify token presence without disclosure

```bash
docker compose exec redmine sh -lc \
  'test -n "$RAG_TRUSTED_SERVICE_TOKEN" && echo configured || echo missing'
docker compose exec rag sh -lc \
  'test -n "$RAG_TRUSTED_SERVICE_TOKEN" && echo configured || echo missing'
```

## Sensitive operational data

Queries and feedback comments may contain sample identifiers or internal details. Restrict database and export access, define retention, avoid logging request bodies, and do not commit CSV exports, dumps, production logs, tokens, credentials, private keys, or `env`.

## Source and model updates

Before dependency, model, source-code, or future LLM changes, review licensing and security implications, run all tests and leakage evaluations, verify offline operation, and back up affected durable state.

## Granting access to internal documentation

Internal Assistant documentation is controlled by the Redmine project-role
permission:

```text
view_internal_assistant_documentation