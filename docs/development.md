# Developer guide

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2.
- Access to a deployment-specific `env` file from the portal maintainers.
  Never commit `env` or `.env` files, and do not use production credentials for
  local development unless explicitly authorized.
- Access to the FoodPort GitHub repository and any Azure DevOps resources
  needed for the task.

The default Compose file is deployment-oriented. Use the local overlay for
editable source code and development server settings:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Check service state and logs with:

```bash
docker compose ps
docker compose logs -f web
```

Stop the local stack without deleting named data volumes:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

Do not use `docker compose down -v` unless you intend to delete persistent
service data.

## Service map

| Service | Responsibility |
|---|---|
| `nginx` | Public HTTP/HTTPS entry point and static-file serving. |
| `web` | Django portal pages and API endpoints. |
| `celeryworker`, `celerybeat`, `redis`, `flower` | Background jobs, scheduled work, queue/broker, and task monitoring. Flower is unauthenticated by default; do not expose it publicly. |
| `batch` | Azure Batch job submission, monitoring, cancellation, and pool operations. |
| `storage` | Azure Blob operations and short-lived transfer URLs. |
| `redmine`, `mariadb` | Redmine and its database. |
| `rag`, `rag-db` | Retrieval API and its PostgreSQL/pgvector database. |

Portal and Celery workflows use the internal Batch and Storage APIs for
provider operations. See the [backend implementation overview](BACKEND_IMPLEMENTATION.md)
for provider boundaries and service ownership.

## Tests and dependency locks

Use the isolated test database and test commands in the [testing
guide](testing.md). The tests do not connect to production databases or submit
jobs to production Azure resources.

Python dependency inputs are `.in` files and lock files are generated `.txt`
files. Regenerate and check them with:

```bash
bash utility/compile_python_requirements.sh
bash utility/compile_python_requirements.sh --check
```

Use Python 3.14 for the portal and Storage locks and Python 3.12 for the Batch
and RAG locks.

## Branch and pull-request workflow

Create feature branches from `dev`:

```bash
git switch dev
git pull --ff-only origin dev
git switch -c my-feature-branch
```

Push completed work to `dev` for CI feedback. Promote tested changes to `main`
with a pull request; `main` requires a passing `Foodport-CI` check. The
`foodport` branch is reserved for production-VM hotfix validation. Bring any
hotfix back to `dev` and `main` so it is not lost in later promotions.

For pipeline operation and deployment-specific configuration, see the
[deployment guide](deployment.md).
