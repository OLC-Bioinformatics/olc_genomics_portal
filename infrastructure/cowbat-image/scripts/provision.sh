#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_CA_SOURCE="/tmp/GoC-GdC-Root-A.crt"
readonly ROOT_CA_DEST="/usr/local/share/ca-certificates/GoC-GdC-Root-A.crt"

if [[ ! -s "$ROOT_CA_SOURCE" ]]; then
  echo "The GoC root CA file is missing or empty." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade -y ca-certificates

install -D -m 0644 "$ROOT_CA_SOURCE" "$ROOT_CA_DEST"
update-ca-certificates

source /usr/bin/miniconda/bin/activate /envs/cowbat
unset PERL5LIB

conda create \
  --yes \
  --prefix /envs/prokka \
  --channel conda-forge \
  --channel bioconda \
  --strict-channel-priority \
  prokka=1.14.6

source /usr/bin/miniconda/bin/activate /envs/prokka

if ! prokka --version; then
  echo "Prokka environment installation failed." >&2
  exit 1
fi

source /usr/bin/miniconda/bin/activate /envs/cowbat

python - <<'PY'
import sys
from pathlib import Path

root_certificate = Path(
    "/usr/local/share/ca-certificates/GoC-GdC-Root-A.crt"
).read_bytes().strip()
bundle = Path(sys.prefix) / "ssl" / "cert.pem"
if not bundle.is_file():
    raise RuntimeError(f"Conda's CA bundle is missing: {bundle}")

contents = bundle.read_bytes()
if root_certificate not in contents:
    bundle.write_bytes(contents.rstrip() + b"\n" + root_certificate + b"\n")
    print(f"Added GoC root CA to Conda's CA bundle: {bundle}")
else:
    print(f"GoC root CA is already in Conda's CA bundle: {bundle}")
PY

python - <<'PY'
import pycurl

curl = pycurl.Curl()
curl.setopt(
    pycurl.URL,
    "https://zenodo.org/record/3786915/files/data.tar.gz?download=1",
)
curl.setopt(pycurl.NOBODY, True)
curl.setopt(pycurl.FOLLOWLOCATION, True)
curl.setopt(pycurl.NOPROGRESS, True)
curl.perform()
status = curl.getinfo(pycurl.RESPONSE_CODE)
curl.close()
if status != 200:
    raise SystemExit(f"MOB-Suite database mirror returned HTTP {status}")
print("MOB-Suite database mirror TLS and HTTP check passed")
PY

database_directory="$(
  python -c \
    'import mob_suite; from pathlib import Path; print(Path(mob_suite.__file__).resolve().parent / "databases")'
)"

if [[ ! -s "$database_directory/status.txt" ]]; then
  rm -f "$database_directory/.lock"
  python -m mob_suite.mob_init
fi

python - "$database_directory" <<'PY'
import sys
from pathlib import Path

database_directory = Path(sys.argv[1])
required_files = (
    "status.txt",
    "ncbi_plasmid_full_seqs.fas",
    "ncbi_plasmid_full_seqs.fas.msh",
)
missing = [
    name
    for name in required_files
    if not (database_directory / name).is_file()
    or (database_directory / name).stat().st_size == 0
]
if missing:
    raise SystemExit(
        f"MOB-Suite database initialization is incomplete; missing or empty: "
        f"{', '.join(missing)}"
    )
print(f"MOB-Suite database verified at {database_directory}")
PY

chmod -R a+rX "$database_directory"
