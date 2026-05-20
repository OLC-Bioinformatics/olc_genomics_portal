import os
import ssl
import urllib3
import requests

CA_BUNDLE = "/home/cfiaadmin/.certs/conda-pypi-ca.pem"

print("Environment variables:")
for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "PIP_CERT"):
    print(f"  {name} = {os.environ.get(name)}")

print("\nssl default verify paths:")
print(ssl.get_default_verify_paths())

print("\nTesting default urllib3 PoolManager:")
try:
    http = urllib3.PoolManager()
    r = http.request("GET", "https://pypi.org/project/redminelib/", timeout=10.0)
    print("  urllib3 default status:", r.status)
except Exception as exc:
    print("  urllib3 default failed:", type(exc).__name__, exc)

print("\nTesting default requests.get:")
try:
    r = requests.get("https://pypi.org/project/redminelib/", timeout=10.0)
    print("  requests default status:", r.status_code)
except Exception as exc:
    print("  requests default failed:", type(exc).__name__, exc)

print("\nTesting explicit CA bundle for requests:")
try:
    r = requests.get("https://pypi.org/project/redminelib/", verify=CA_BUNDLE, timeout=10.0)
    print("  requests explicit status:", r.status_code)
except Exception as exc:
    print("  requests explicit failed:", type(exc).__name__, exc)

print("\nTesting explicit CA bundle for urllib3:")
try:
    http = urllib3.PoolManager(ca_certs=CA_BUNDLE)
    r = http.request("GET", "https://pypi.org/project/redminelib/", timeout=10.0)
    print("  urllib3 explicit status:", r.status)
except Exception as exc:
    print("  urllib3 explicit failed:", type(exc).__name__, exc)
