# Retrieval Evaluation

## Run the full evaluation

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --questions /app/tests/evaluation_questions.yaml \
  --top-k 5 \
  --output-json /tmp/evaluation-current.json

docker compose cp rag:/tmp/evaluation-current.json \
  rag/evaluation-results/evaluation-current.json
```

## Quality gates

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --questions /app/tests/evaluation_questions.yaml \
  --top-k 5 \
  --minimum-hit-at-5 1.0 \
  --minimum-requirement-at-5 1.0 \
  --minimum-term-hit-rate 0.94 \
  --minimum-heading-hit-rate 0.90 \
  --output-json /tmp/evaluation-gated.json
```

Adjust gates only through reviewed baseline changes, not to hide regressions.

## Evaluate a category

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --category troubleshooting \
  --top-k 5
```

Use category names present in `rag/tests/evaluation_questions.yaml`.

## Abstention evaluation

Questions with `should_abstain: true` verify unsupported queries. The evaluation system supports a default score gate:

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --top-k 5 \
  --abstention-max-score 0.60 \
  --output-json /tmp/evaluation-abstention.json
```

Add several classes of negative questions: unrelated text, unrelated software, unsupported bioinformatics workflows, and plausible but undocumented Automator questions. Also keep difficult valid paraphrases so threshold tuning does not destroy recall.

## Compare candidate thresholds

For each candidate value, recreate RAG, run the evaluation, and archive a separately named report:

```bash
# Edit RAG_MINIMUM_SIMILARITY in docker-compose.yml first.
docker compose up -d --force-recreate rag

docker compose exec rag python -c \
  'from config import settings; print(settings.minimum_similarity)'

docker compose exec -w /app rag python -m cli evaluate \
  --top-k 5 \
  --output-json /tmp/evaluation-threshold-060.json

docker compose cp rag:/tmp/evaluation-threshold-060.json \
  rag/evaluation-results/evaluation-threshold-060.json
```

Select the lowest threshold that rejects most unsupported questions without materially reducing valid Hit@1/3/5, source requirements, heading terms, or content terms.

## When to add more retrieval complexity

- Add documentation or synonyms when users use stable alternate terms absent from the source.
- Improve chunks when the correct source is retrieved but required context is split or missing.
- Consider hybrid keyword/vector retrieval when exact identifiers and parameter names are under-ranked.
- Consider reranking when useful candidates are retrieved but ordered poorly.
- Do not add optional complexity without evaluation evidence.
