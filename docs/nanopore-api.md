# Nanopore API

This API creates Nanopore sequencing runs, prepares direct Azure Blob uploads,
verifies uploaded POD5 files, starts incremental processing, publishes batch
reports, and terminates an open run on operator request.

## Current status

The request and response examples below are the current integration contract.
The immutable GPU runtime is validated in image
`development/nanopore/0.0.13`. The repository contains the Django model,
endpoint, immutable-manifest, Celery submission, capacity-retry, Batch polling,
long-lived task, iteration-result publication, and latest-result URL slices.
The GPU acceptance validates the image and scientific runtime. The remaining
deployment check is a live API-to-Batch smoke test with target credentials; it
is not a missing implementation slice.

The current one-POD5 GPU acceptance is documented in
`infrastructure/nanopore-image/docs/acceptance-results/0.0.13`.
That acceptance validates the scientific runtime and image-level streaming
invariants; it does not replace a target-environment API integration test.

The GUI must use `finalize` to stop intake. There is no separate `cancel`,
`retry`, artifact-listing, or events endpoint in the current minimum contract.
The GUI obtains progress and completed iteration pointers from the run-detail
response.

The API base URL is:

```text
https://foodport-dev.cloud-nuage.inspection.gc.ca/en-ca/api
```

All requests must use HTTPS.

## Authentication

The native GUI uses portal-assisted pairing to issue a short-lived DRF token.
It does not collect a FoodPort password or store a browser session cookie.

### Start pairing

```http
POST /auth/pairing/start/
Content-Type: application/json
```

The response contains a short-lived pairing identifier and the browser URL
where the operator signs in with Microsoft Entra ID and approves the GUI:

```json
{
  "pairing_id": "random-public-id",
  "approval_url": "https://foodport-dev.cloud-nuage.inspection.gc.ca/nanopore/pair/authorize/random-public-id",
  "expires_in": 300
}
```

### Exchange the approved code

```http
POST /auth/pairing/exchange/
Content-Type: application/json
```

```json
{
  "pairing_id": "random-public-id",
  "code": "482913"
}
```

The response is a short-lived FoodPort token:

```json
{
  "token": "0123456789abcdef...",
  "expires_in": 3600
}
```

Pairing IDs and codes are one-time credentials. The GUI keeps them in memory
only and clears the FoodPort token on sign-out. The legacy `/auth/token/`
endpoint may remain available for existing non-GUI clients, but it is not part
of the native GUI workflow.

The native GUI uses portal-assisted pairing rather than collecting a FoodPort
password. It starts pairing with `POST /auth/pairing/start/`, opens the
returned `approval_url` in the user's browser, and exchanges the approved
one-time code with `POST /auth/pairing/exchange/`. On sign-out it calls
`POST /auth/entra/logout/` and clears the in-memory FoodPort token.

Send the token on every protected Nanopore request:

```http
Authorization: Token 0123456789abcdef...
```

Do not log the token or password. A GUI should keep the token in protected application storage and clear it when the user logs out.

## Create a run

```http
POST /nanopore/runs/
Authorization: Token <token>
Content-Type: application/json
```

```json
{
  "run_name": "260825-nanopore",
  "barcode_kit": "SQK-RBK114-24",
  "barcode_values": [1],
  "sample_metadata": {
    "samples": [
      {
        "barcode": 1,
        "seqid": "2026-MIN-0001",
        "olnid": "OLN-1"
      }
    ]
  }
}
```

`barcode_values` contains numeric Dorado barcode numbers and must match the
sample metadata rows. `sample_metadata` is built from the PoreSippR-style
sample table; each selected barcode requires both a SEQID and an OLNID.
The backend supplies the fixed reference, Dorado model, output paths, device,
and scheduler runtime settings.

`run_name` must use the existing FoodPort format, for example `260825-nanopore`.

Response: `201 Created`

```json
{
  "run_id": 123,
  "run_name": "260825-nanopore",
  "workflow_state": "created"
}
```

The run is open for additional files after creation. The first completed file
creates the first immutable input-manifest generation and starts one long-lived
Azure Batch task for the run. Later completed files create later generations
and schedule publication of those manifests while the same task is active;
they do not create additional Batch tasks.

The run belongs to the authenticated user. Another user cannot access it.

## Prepare a POD5 upload

Call this once for each local POD5 file:

```http
POST /nanopore/runs/123/files/prepare/
Authorization: Token <token>
Content-Type: application/json
```

```json
{
  "relative_path": "pass/sample-a.pod5",
  "size_bytes": 524288000
}
```

Only relative paths ending in `.pod5` are accepted. The path must not contain `..` path components.

Response: `200 OK`

```json
{
  "file_id": 456,
  "upload_url": "https://carlingst01.blob.core.windows.net/...sas-token...",
  "blob_name": "260916-nanopore/input/pod5/pass/sample-a.pod5"
}
```

Completion is idempotent. Repeating it after a successful completion returns
the same file state; it does not duplicate the file in a later manifest.

The SAS URL expires after approximately 30 minutes. Request a new URL if the upload needs to be retried later.

## Upload directly to Azure Blob

Do not send POD5 bytes through the FoodPort API. Upload the file directly to the returned SAS URL:

```javascript
const uploadResponse = await fetch(upload_url, {
  method: "PUT",
  headers: {
    "x-ms-blob-type": "BlockBlob",
    "Content-Type": "application/octet-stream"
  },
  body: pod5File
});

if (!uploadResponse.ok) {
  throw new Error("POD5 upload failed");
}
```

The Azure storage account must allow the GUI origin through Blob CORS and allow the `PUT` method with the `x-ms-blob-type` header.

## Mark a file complete

After the direct Blob upload succeeds:

```http
POST /nanopore/runs/123/files/456/complete/
Authorization: Token <token>
```

The API checks that the Blob exists and that its size equals `size_bytes` from
the file record. Completion claims the file into the next immutable generation
and schedules the existing task or its manifest publisher.

Response: `200 OK`

```json
{
  "run_id": 123,
  "run_name": "260825-nanopore",
  "workflow_state": "uploading",
  "status": "Unprocessed",
  "files": [
    {
      "id": 456,
      "relative_path": "pass/sample-a.pod5",
      "size_bytes": 524288000,
      "sha256": "",
      "status": "uploaded"
    }
  ]
}
```

## Get run status

```http
GET /nanopore/runs/123/
Authorization: Token <token>
```

The response includes the run state and every registered file. The GUI polls
this endpoint while uploading, while processing, and during termination.

When processing has started, the response includes a `processing` object with
the run-level task state, processed-file counters, and generation records. Each
completed generation is listed in `processing.reports` with its generation,
iteration, and immutable `report_url` blob name. The GUI should render each
iteration as it becomes available and retain earlier iterations.

To retrieve one iteration:

```http
GET /nanopore/runs/123/results/2/
Authorization: Token <token>
```

To retrieve the newest published iteration:

```http
GET /nanopore/runs/123/results/latest/
Authorization: Token <token>
```

The response is a short-lived authenticated read URL and metadata for the
immutable result manifest:

```json
{
  "run_id": 123,
  "generation": 2,
  "iteration": 2,
  "report_url": "https://carlingst01.blob.core.windows.net/...sas-token...",
  "report_blob": "runs/260825-nanopore/iterations/iteration-000002/manifests/iteration-000002.json"
}
```

The GUI must treat `report_blob` as an opaque identifier and use `report_url`
for the download. It must not construct Azure paths or retain the SAS URL
beyond the immediate download.

While the run is open, the response should also expose completed batch reports
in `status.report` or `status.processing.report`. Reports must be cumulative,
or include stable batch and iteration identifiers so the GUI can retain prior
pages as later batches finish.

Possible run states currently include:

- `created`
- `uploading`
- `processing`
- `stopping`
- `complete`
- `error`

Possible file states currently include:

- `prepared`
- `uploaded`

## Finalize a run

After all expected POD5 files have been completed, stop intake and request a
graceful shutdown:

```http
POST /nanopore/runs/123/finalize/
Authorization: Token <token>
Content-Type: application/json
```

The request is idempotent. It prevents new files from entering a manifest,
publishes a final control marker, and signals the existing Batch task to finish
after all already-published generations are processed. The task can also stop
because of walltime, cancellation/preemption, scheduler or VM error, or an
internal processing error. Finalization does not invalidate already-published
iteration results.

The GUI should continue polling `GET /nanopore/runs/123/` until the run reaches
`complete` or `error`; `stopping` means finalization was accepted but the task
has not exited yet.
- `error`

## Terminate a run

The GUI stops discovering new files, drains uploads already in progress, and
then requests graceful processing termination:

```http
POST /nanopore/runs/123/finalize/
Authorization: Token <token>
```

Response: `200 OK`

```json
{
  "run_id": 123,
  "workflow_state": "stopping",
  "processing_status": "stopping",
  "termination_requested_at": "2026-09-15T18:00:00Z"
}
```

Termination must be idempotent. It must prevent new files from entering a
future batch, allow the active batch to finish or be explicitly cancelled,
signal the Batch VM/task to shut down, and publish a final state and report.

The previous single-manifest behavior is legacy mode only. See
`BACKEND_STREAMING_REQUIREMENTS.json` for the machine-readable backend
requirements.

FoodPort submits one Batch job/task per run, not one task per generation. The
task remains active while uploads continue and stops on the Azure Batch
walltime, an explicit finalize control marker, a scheduler/VM error, task
cancellation or preemption, or another terminal Batch failure.

The deployment defines `NANOPORE_BATCH_COMMAND` as a shell command template
executed by the Batch node. The current command invokes the installed
`foodport-nanopore-task` wrapper with the immutable manifest, raw/results
containers, task paths, scheduler, and CUDA device. The wrapper accepts the
manifest and runs Dorado, minimap2, samtools, and target detection.

The web application publishes manifests and control state through the internal
Storage service and submits work through the shared Batch service. It creates
expiring container transfer URLs for the wrapper and passes them as
`FOODPORT_INPUT_SAS_URL` and `FOODPORT_OUTPUT_SAS_URL` task environment
variables. Their lifetime is controlled by
`NANOPORE_BATCH_TRANSFER_URL_TTL_SECONDS` (default: 604800 seconds); the URLs
are redacted from application logs.

The wrapper reads POD5 entries from each immutable generation manifest,
basecalls with Dorado, aligns with minimap2, produces sorted/indexed BAM files
with samtools, publishes the generation result, and then polls for the next
generation. The old Guppy CSV format is not used by this submission contract.

If `NANOPORE_BATCH_COMMAND` is missing, the processing record is marked
`failed` and the run is marked `error`. When configured, the processing status
becomes `submitted` after the Batch service returns a job and task ID. Azure
GPU capacity errors are retried with delayed backoff, up to
`NANOPORE_SUBMISSION_MAX_RETRIES` times (default: 8). While those retries are
pending, the status response includes
`processing.retryable: true`, `processing.retry_count`,
`processing.retry_limit`, and a user-safe `processing.message`. If capacity
remains unavailable, the processing status becomes `failed` with an actionable
terminal message; other submission errors fail immediately.

## Latest result

After processing reaches `complete`, request the latest published result:

```http
GET /nanopore/runs/123/results/latest/
Authorization: Token <token>
```

The response supplies a short-lived result URL, commonly under
`result_url`, `download_url`, `result_manifest_url`, or
`latest_result_url`, and may include an `outputs` list. The GUI keeps this URL
only in memory for the operator's result link; it does not save the URL in the
local queue.

For the in-app target report, the published result manifest may also include a
`report` object with one entry per scheduler iteration:

```json
{
  "report": {
    "gdcs_total": 325,
    "reference": {
      "name": "PoreSippRDB_240509.fasta",
      "sha256": "<sha256>"
    },
    "iterations": [
      {
        "iteration": 1,
        "rows": [
          {
            "SEQID": "2026-MIN-0001",
            "OLN ID": "OLN-TEST-01",
            "O-Type": "157 (12)",
            "H-Type": "7 (10)",
            "stx1": 8,
            "stx2": "-",
            "eae": 4,
            "hylA": "-",
            "aggR": "-",
            "aaiC": "-",
            "uidA": 9,
            "GDCS": "4/325",
            "Coverage": 8.25
          }
        ]
      }
    ]
  }
}
```

`gdcs_total` must be calculated by the backend from the exact reference panel
used for the run, or from its authoritative `.fai`/annotation manifest. It must
not be inferred from the sparse hit rows. The GUI treats the value as metadata
and displays each row's GDCS hit count against that denominator.

The GUI displays each iteration as a page and applies the report
colouring: `-` is white, numeric values below `7.5` are grey, coverage-like
fractions below `320` are grey, and positive values are blue. The backend
should retain every iteration in this list; the GUI does not overwrite earlier
pages.

## Error handling

- `400 Bad Request`: invalid input, missing Blob, or invalid POD5 path.
- `401 Unauthorized`: missing or invalid token.
- `403 Forbidden`: authentication was not supplied for a protected endpoint.
- `404 Not Found`: run or file does not exist or belongs to another user.
- `409 Conflict`: duplicate/conflicting file manifest, closed run, size mismatch, or incomplete finalization.

The GUI should stop the workflow and display the server error when a FoodPort
request returns a non-2xx response. It should not retry `400`, `401`, `403`, or
`404` automatically, except that an upload-phase Azure `401` or `403` causes
the GUI to request a fresh per-blob SAS URL and retry the upload.

## JavaScript request helper

```javascript
const API_BASE = "https://foodport-dev.cloud-nuage.inspection.gc.ca/en-ca/api";

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Token ${token}`,
      ...options.headers
    }
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const error = await response.json();
      detail = error.detail || error.error || detail;
    } catch (ignored) {
      // Keep the HTTP status text when the response is not JSON.
    }
    throw new Error(`${response.status}: ${detail}`);
  }

  return response.status === 204 ? null : response.json();
}
```

For the direct Azure upload, use the SAS `upload_url` without the FoodPort `Authorization` header. The SAS URL itself grants the temporary upload permission.
