# Documentation Indexing

## Source discovery

The RAG container reads Markdown documentation from:

```text
/documentation
```

The mount must be read-only. At the current baseline, 51 documents produce approximately 405 chunks.

## Run the indexer

```bash
docker compose exec \
  -w /app \
  rag \
  python -m cli index
```

The indexer:

1. Discovers Markdown files.
2. Calculates SHA-256 source checksums.
3. Skips unchanged documents.
4. Parses changed documents.
5. Generates stable chunks.
6. Embeds each chunk's contextual embedding content.
7. Atomically replaces changed document rows and chunks.
8. Removes deleted documents after a fully successful run.
9. Stores index configuration metadata.

## Expected first run

```text
Documents discovered: 51
Documents added: 51
Documents updated: 0
Documents unchanged: 0
Documents removed: 0
Chunks embedded: approximately 405
Failures: 0
```

## Expected unchanged run

```text
Documents discovered: 51
Documents added: 0
Documents updated: 0
Documents unchanged: 51
Documents removed: 0
Chunks embedded: 0
Failures: 0
```

## Verify index status

```bash
docker compose exec rag python -m cli status
```

## Verify all chunks have vectors

```bash
docker compose exec rag-db \
  psql \
  -U redmine_assistant \
  -d redmine_assistant \
  -tAc \
  "SELECT COUNT(*), COUNT(embedding) FROM document_chunks;"
```

Both counts must match.

## Verify vector dimensions

```bash
docker compose exec rag-db \
  psql \
  -U redmine_assistant \
  -d redmine_assistant \
  -tAc \
  "SELECT vector_dims(embedding), COUNT(*)
   FROM document_chunks
   GROUP BY vector_dims(embedding);"
```

Expected vector dimension:

```text
384
```

## When to re-index

Run the indexer after:

- adding, editing, moving, or removing documentation;
- changing chunk-generation logic;
- changing embedding content construction;
- restoring an older vector database;
- intentionally rebuilding with a different embedding configuration.

## Full re-index conditions

A full rebuild is required after changing:

- embedding model name;
- model revision;
- vector dimension;
- embedding normalization;
- chunk-size settings;
- material parsing/chunking behaviour.

The index metadata guard is intended to prevent incompatible vectors from being mixed.
