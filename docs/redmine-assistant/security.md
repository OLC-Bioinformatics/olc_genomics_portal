# Security and Access Control

## Trust boundaries

- Users authenticate to Redmine through Entra ID.
- The browser does not call the RAG API directly.
- Redmine proxies validated searches to `http://rag:8001/search` on the internal Compose network.
- The RAG API binds to host loopback and is not intended as a public user endpoint.

## Redmine permission

The plugin registers:

```text
view_redmine_assistant
```

Grant it only to appropriate Redmine roles under **Administration → Roles and permissions**.

## Internal documentation

Internal chunks are tagged with:

```text
access_level=internal
```

Protection exists at multiple layers:

1. Standard RAG searches exclude internal chunks in SQL.
2. The public RAG API always calls retrieval with internal access disabled.
3. The Redmine controller does not forward an internal-access option.
4. The RAG API defensively removes internal results.
5. The Redmine plugin defensively discards internal results and `internal_only/` paths.
6. Evaluation and automated tests check for leakage.

Do not remove any layer without a formal security review.

## Input controls

- Query must be non-empty.
- Redmine limits query length to 2,000 characters.
- RAG limits result count to `RAG_MAX_TOP_K`.
- Redmine sends only `query` and configured `limit`.
- Requests are protected by Redmine authentication, authorization, and CSRF controls.

## Output controls

- RAG errors are converted to generic user-facing messages.
- Raw exception details are logged server-side, not shown to users.
- Documentation source paths are normalized and restricted.
- `internal_only/`, absolute paths, null bytes, and parent-directory traversal are rejected.
- Documentation content is rendered through Redmine’s configured CommonMark formatter and sanitizer.
- External links use `target="_blank"` with `rel="noopener noreferrer"`.

## Secret handling

Do not commit:

- `env` files containing secrets;
- database passwords;
- Entra ID client secrets;
- SMTP credentials;
- private keys;
- database dumps;
- production logs containing sensitive request context.

## Dependency and model updates

Before updating Python, Ruby, Rails, Redmine, pgvector, model, or transformer dependencies:

1. Review release and security notes.
2. Build in a non-production environment.
3. Run all tests.
4. Run the retrieval evaluation.
5. Verify offline model loading.
6. Review access-control tests and live leakage smoke tests.
7. Back up affected databases.
