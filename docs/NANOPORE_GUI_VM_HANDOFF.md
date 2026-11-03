# Nanopore GUI and VM Handoff

Current acceptance evidence:
`infrastructure/nanopore-image/docs/acceptance-results/0.0.13`

## GUI/API contract

The GUI owns the operator workflow; Azure Batch owns the long-running analysis.
There is exactly one Batch task for an open run. Upload completion adds work to
the run by publishing a new immutable manifest generation, and the task polls
for those generations in its existing workspace.
Objective:
Document and operate the Nanopore GUI -> FoodPort API -> Azure Blob -> Azure
Batch -> results workflow.

Accepted runtime:
- Image: development/nanopore/0.0.13
- VM size: Standard_NV18ads_A10_v5
- GPU: NVIDIA A10-12Q, 12 GiB
- Dorado: 2.1.2+8b8fc5d
- Model: dna_r10.4.1_e8.2_400bps_fast@v5.2.0
- PoreSippR commit: 4f538ae0d4522667915bca1002132f6a3bc523ae
- Targets: /opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta
- Target SHA-256: 6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9
- Target records: 6663

Accepted single-file result:
- 1 POD5 file processed as 1 scheduler batch
- scheduler run twice with duplicate processing prevented
- 4 demultiplexed result sets and 4 mapping BAM/BAI pairs
- barcode 22 mapping validation: gntK=1
- evidence: `infrastructure/nanopore-image/docs/acceptance-results/0.0.13`

Runtime paths:
- Scheduler:
  /opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
- Task wrapper:
  /usr/local/bin/foodport-nanopore-task
- Python:
  /opt/micromamba/root/envs/poresippr/bin/python
- Targets:
  /opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta

GUI/API contract:
- GET /microsoft_authentication/login (browser sign-in)
- GET /microsoft_authentication/logout (browser sign-out)
- POST /auth/token/ (legacy automation only)
- POST /auth/entra/exchange/ (optional standalone public-client flow)
- POST /auth/entra/logout/ (native GUI token revocation)
- POST /auth/pairing/start/ (start portal-assisted pairing)
- POST /auth/pairing/exchange/ (exchange approved one-time code)
- GET/POST /auth/pairing/approve/{pairing_id}/ (browser approval page)
- POST /nanopore/runs/
- POST /nanopore/runs/{run_id}/files/prepare/
- PUT returned short-lived SAS URL
- POST /nanopore/runs/{run_id}/files/{file_id}/complete/
- POST /nanopore/runs/{run_id}/finalize/
- GET /nanopore/runs/{run_id}/

Required properties:
- Browser users authenticate with their own Entra ID identity; do not create
  shared GUI accounts or ask users for a FoodPort password.
- The native GUI uses portal-assisted pairing when it cannot use a separately
  registered Entra public client: start a pairing, open the returned approval
  URL in the system browser, and exchange the displayed one-time code.
- Pairing codes are hashed, expire after five minutes, require an authenticated
  portal user and CSRF-protected approval, and can be exchanged only once.
- The browser approval page uses the Django session cookie after Microsoft
  login and requires CSRF protection. The native GUI does not use that session;
  it exchanges the approved pairing code for a short-lived API token.
- The GUI and API should use the same site/origin where practical. If the GUI
  is hosted separately, configure an explicit trusted origin and CORS policy;
  never use wildcard credentialed CORS.
- File preparation and completion are idempotent.
- Each completed file can enter an immutable batch manifest while the run is open.
- The first completed file starts the long-lived Batch task.
- The GUI never receives storage account keys.
- The GUI never stores long-lived SAS credentials.
- Batch processing uses immutable manifest generations, never an uncontrolled live blob listing.
- Finalization stops intake and signals graceful Batch VM/task shutdown.
- Results are published immutably and exposed through the result-manifest URL.

Verification status:
- GPU scientific runtime is accepted in image `development/nanopore/0.0.13`.
- The GPU acceptance processed one POD5 file, ran the scheduler twice, prevented
  duplicate processing, produced four demultiplexed result sets and BAM/BAI
  pairs, and validated `barcode22 -> gntK = 1`.
- The focused PoreSippR wrapper/scheduler suite passes: `60 passed`.
- Backend API and task tests cover authenticated ownership, idempotent file
  completion, immutable manifest generations, one-task streaming ownership,
  iteration result URLs, finalize idempotency, and termination signaling.
- Deployment-level authenticated GUI -> API -> Blob -> Batch connectivity still
  requires a target-environment smoke test; this is operational verification,
  not a missing implementation protocol.

## GUI behavior

1. Pair once, keep the short-lived API token in memory or protected app storage,
   and clear it on logout.
2. Create one run and retain its `run_id`.
3. For each POD5, prepare a file, upload directly to the returned Blob SAS URL,
   then complete the file. Never send POD5 bytes through Django. Completion
   creates or queues an immutable manifest generation.
4. Poll the run detail endpoint. Display uploaded-file progress, processing
   counters, task state, and every completed `processing.reports` entry.
5. Fetch each iteration from its result endpoint. Each completed generation is
  published before the wrapper advances to the next one. Use the returned SAS URL
   immediately and treat the blob name as opaque.
6. After the operator indicates that intake is complete, call `finalize` once.
   Disable new-file controls after it succeeds, but keep polling until
   `complete` or `error`.

The GUI must not assume that one upload equals one Batch task, that iterations
finish in upload order, or that the newest result replaces earlier results.
Keep iteration and generation identifiers in the local view model.
Authentication implementation notes:
- The portal already includes `microsoft_authentication` routes and maps a
  successful Entra login to a Django session/user.
- Validate that the deployed Entra redirect URI exactly matches
  `SITE_URL/microsoft_authentication/callback`.
- Keep `DJANGO_DEBUG=False` in production. The production settings default to
  false, but the deployment should set it explicitly as an operational check.
- If a future standalone SPA needs bearer tokens, register a public Entra
  client and use authorization code plus PKCE. The backend must validate issuer,
  tenant, signature, audience, expiry, scopes/roles, and the user's identity
  before applying run ownership checks.