# Nanopore Cloud Workflow GUI

A small local PySide6 application for laboratory operators. It creates a FoodPort Nanopore run, watches a selected directory for stable `.pod5` files, uploads them directly to Azure Blob Storage using short-lived SAS URLs, confirms each upload with FoodPort, and shows run progress.

The app does not contain Dorado, minimap2, samtools, or Azure account credentials. Processing remains a FoodPort cloud responsibility.

## Run locally

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m nanopore_gui
```

Set `FOODPORT_API_BASE` to override the development API URL. The local queue is stored under the platform application-data directory; passwords, tokens, and SAS URLs are never written there.

For the current development FoodPort deployment, TLS certificate verification
is disabled by default for both FoodPort and Azure requests. Set
`FOODPORT_VERIFY_SSL=true` to enable verification, or set `FOODPORT_CA_BUNDLE`
to the path of a trusted private CA bundle. The current default is temporary,
vulnerable to man-in-the-middle attacks, and must not be used for production
or sensitive data.

Example development launch:

```powershell
$env:FOODPORT_VERIFY_SSL = "false"
py -m nanopore_gui
```

## Current workflow

1. Open FoodPort in the normal browser, sign in with Microsoft, approve Nanopore GUI pairing, and enter the one-time code.
2. Enter a run name, choose the barcode kit, select the used barcodes, enter each sample's SEQID/OLNID, and create the run.
3. Select the MinKNOW POD5 output directory.
4. Let the app discover files after the stability interval and upload them.
	FoodPort starts one long-lived Batch task from the first completed file and
	queues later completed files as immutable manifest generations.
5. Leave the GUI running while completed files upload and incremental reports
	arrive. Each iteration maps cumulative retained FASTQ fragments. Use
	**Finalize run** only when sequencing is finished; it stops new file intake
	and requests graceful Batch shutdown.

The default file stability interval is 180 seconds and the default upload concurrency is three workers; both can be changed in Run setup. Files are detected promptly through a filesystem watcher and are also rediscovered by the periodic reconciliation scan. Failed uploads are listed in the dashboard and can be retried. The local queue survives application restart, including an interrupted upload.

After a run is created, the GUI polls the FoodPort status endpoint every two
seconds. It displays the workflow and processing states, processed POD5 counts
when supplied, incremental batch reports, the last successful status refresh,
published output names, and the server-provided result-manifest link. The GUI
submits `barcode_kit`, selected numeric `barcode_values`, and PoreSippR-style
`sample_metadata` rows containing barcode, SEQID, and OLNID values. Dorado
model, reference, output, device, and scheduler settings remain backend
configuration.

The legacy scheduler CSV is intentionally not required: the cloud API receives a run manifest and the server owns processing configuration. A CSV adapter can be added later if the server needs compatibility with the existing scheduler.

The desktop pairing flow uses `POST /auth/pairing/start/` and
`POST /auth/pairing/exchange/`. Pairing IDs and codes remain in memory only;
the GUI never receives a FoodPort password, Entra client secret, or browser
session cookie.

## API assumptions

The implementation follows `nanopore-api.md`: token authentication, run creation/status, per-file prepare, direct `PUT` to the returned SAS URL, file completion, and finalize. FoodPort and Azure Blob CORS must allow the workstation's network path and required headers.
