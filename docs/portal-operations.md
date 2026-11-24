# Portal operations runbook

Run commands from the portal repository on an authorized host. These checks
are intended for diagnosis; avoid restarting or resubmitting work until you
understand the impact.

## Initial triage

1. Record the UTC time, affected service, and user-visible run or request ID.
2. Check container state:

   ```bash
   docker compose ps -a
   ```

3. Review recent logs for the affected service:

   ```bash
   docker compose logs --since 15m --tail 300 web celeryworker celerybeat
   ```

   Replace the service names with the affected Compose services. Review logs
   before sharing them; remove sample identifiers and other sensitive details.
4. Use the health checks below to distinguish an application issue from a
   Batch, Storage, Redis, or RAG dependency issue.
5. For a production deployment issue, check the Azure DevOps run and follow the
   [deployment guide](deployment.md). Do not manually switch the production
   checkout or rebuild services to bypass a failed pipeline.

## Service checks

| Area | Check |
|---|---|
| Batch API | `curl -fsS http://127.0.0.1:5000/health` |
| Storage API | `docker compose exec storage python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5001/health', timeout=4).read().decode())"` |
| Redis broker | `docker compose exec redis redis-cli ping` should return `PONG`. |
| RAG API | `curl -fsS http://127.0.0.1:8001/health/ready` |
| RAG database | `docker compose exec rag-db pg_isready -U redmine_assistant -d redmine_assistant` |
| Web, Nginx, Redmine, Celery, MariaDB | Check `docker compose ps -a` and the relevant service logs. These services do not all have Compose health checks, so `running` alone does not prove that the application is usable. |

RAG-specific diagnosis and database checks are in the [Redmine Assistant
operations runbook](redmine-assistant/operations-runbook.md). The RAG database
backup and restore procedure is in its [backup guide](redmine-assistant/backup-and-restore.md).

## Background jobs

For a request that remains **Processing**, capture its ID and submission time,
then inspect `celeryworker`, `celerybeat`, `redis`, `batch`, and `storage`
logs/state. Check the corresponding job in Azure Batch if the internal APIs
are healthy. Do not submit a duplicate or cancel a Batch job until the request
owner confirms that action is safe.

Flower is available on host port `8888` without authentication in the current
Compose configuration. Use it only from a restricted, trusted network; do not
expose it publicly.

## Upload and storage issues

For an upload failure, record the run ID and failed filename, then check the
Storage API and the relevant application logs. If the interface offers a
retry, use it for the failed upload rather than creating a duplicate run.
Never include SAS URLs, tokens, or credentials in a support request.

## Recovery and persistent data

- Check logs and service dependencies before restarting anything.
- For production code or image changes, use the authorized deployment pipeline
  described in the [deployment guide](deployment.md).
- A container restart does not apply changed Compose environment values;
  configuration changes require container recreation through the deployment
  process.
- Do not use `docker compose down -v`, `docker volume rm`, or volume-pruning
  commands during routine recovery. Named volumes contain Redis, Redmine DB,
  RAG DB, and model-cache state.
- The RAG database has a documented backup procedure. Follow the
  deployment-specific backup process for Redmine MariaDB and the portal's
  configured application database; do not assume that Docker volumes are
  backups.

## Escalation

Provide the affected service, UTC timestamps, request/run ID, relevant
deployment run, and sanitized error messages to the portal maintainers. Do not
send credentials, `env` files, private keys, SAS URLs, or raw sensitive
sequence data.
