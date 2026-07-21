# Helpfulness Feedback

## Purpose

Feedback is evaluation telemetry. It does not automatically train the embedding model or a future LLM. The plugin collects feedback; the trusted RAG API validates and stores it in PostgreSQL.

## Tables

- `retrieval_requests`: request UUID, query, access context, result count, chunk keys, and timestamp.
- `retrieval_feedback`: rating, controlled reason, optional comment, target type, and timestamps.

Reindexing must not delete either table. One feedback record is stored per retrieval response and target type; a later submission updates that record.

## Recent searches and feedback

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT rr.created_at, rr.request_id, rr.query, rr.access_context,
       rr.result_count, rf.rating, rf.reason, rf.comment,
       rf.updated_at AS feedback_updated_at
FROM retrieval_requests rr
LEFT JOIN retrieval_feedback rf ON rf.request_id = rr.request_id
ORDER BY rr.created_at DESC
LIMIT 50;"
```

## Submitted feedback only

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT rr.created_at AS searched_at, rf.updated_at AS feedback_at,
       rr.query, rr.access_context, rr.result_count,
       rr.result_chunk_keys, rf.rating, rf.reason, rf.comment
FROM retrieval_feedback rf
JOIN retrieval_requests rr ON rr.request_id = rf.request_id
ORDER BY rf.updated_at DESC;"
```

## Summary and participation rate

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT rating, COUNT(*) AS feedback_count
FROM retrieval_feedback GROUP BY rating ORDER BY rating;"

docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT COUNT(*) AS total_searches,
       COUNT(rf.id) AS rated_searches,
       COUNT(*)-COUNT(rf.id) AS unrated_searches,
       ROUND(100.0*COUNT(rf.id)/NULLIF(COUNT(*),0),1) AS feedback_rate_percent
FROM retrieval_requests rr
LEFT JOIN retrieval_feedback rf ON rf.request_id=rr.request_id;"
```

## Unhelpful reasons

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT reason, COUNT(*) AS count
FROM retrieval_feedback
WHERE rating='unhelpful'
GROUP BY reason ORDER BY count DESC, reason;"
```

Interpret reasons as follows:

- `irrelevant_results`: inspect threshold, query phrasing, chunking, and ranking.
- `missing_documentation`: add or expand approved documentation.
- `unclear_documentation`: rewrite the authoritative section.
- `outdated_documentation`: update and reindex the source.
- `insufficient_detail`: improve source coverage or chunk context.
- `other`: review the bounded free-text comment.

## Zero-result queries

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT created_at, query, access_context
FROM retrieval_requests
WHERE result_count=0
ORDER BY created_at DESC;"
```

Review these to distinguish correct abstentions from valid questions filtered by an overly high threshold.

## CSV export

```bash
docker compose exec -T rag-db psql \
  -U redmine_assistant -d redmine_assistant --csv -c "
SELECT rr.created_at AS searched_at, rf.updated_at AS feedback_at,
       rr.request_id, rr.query, rr.access_context, rr.result_count,
       array_to_string(rr.result_chunk_keys,';') AS result_chunk_keys,
       rf.rating, rf.reason, rf.comment
FROM retrieval_feedback rf
JOIN retrieval_requests rr ON rr.request_id=rf.request_id
ORDER BY rf.updated_at DESC;" > redmine_assistant_feedback.csv

head -n 5 redmine_assistant_feedback.csv
```

Do not commit the export. Queries and comments may contain sensitive operational information.

## Retention cleanup

Define an approved retention period before automating deletion. A deletion from `retrieval_requests` cascades to matching feedback. Back up first and test the query with `SELECT` before replacing it with `DELETE`.
