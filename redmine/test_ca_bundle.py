import os
import ssl
import socket
import urllib3
import requests

# Adjust this path if needed
CA_BUNDLE = "/home/cfiaadmin/.certs/conda-pypi-ca.pem"

# Set the env vars for this process
os.environ["REQUESTS_CA_BUNDLE"] = CA_BUNDLE
os.environ["SSL_CERT_FILE"] = CA_BUNDLE
os.environ["PIP_CERT"] = CA_BUNDLE

print("Env vars:")
for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "PIP_CERT"):
    print(f"  {name} = {os.environ.get(name)}")

print("\nSSL default verify paths:")
print(ssl.get_default_verify_paths())

print("\nTesting raw ssl handshake:")
try:
    ctx = ssl.create_default_context(cafile=CA_BUNDLE)
    with socket.create_connection(("pypi.org", 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname="pypi.org") as ssock:
            print("  raw ssl ok:", ssock.version())
except Exception as exc:
    print("  raw ssl failed:", type(exc).__name__, exc)

print("\nTesting urllib3 default PoolManager:")
try:
    http = urllib3.PoolManager()
    r = http.request("GET", "https://pypi.org/project/redminelib/", timeout=10.0)
    print("  urllib3 default status:", r.status)
except Exception as exc:
    print("  urllib3 default failed:", type(exc).__name__, exc)

print("\nTesting urllib3 explicit CA bundle:")
try:
    http = urllib3.PoolManager(ca_certs=CA_BUNDLE)
    r = http.request("GET", "https://pypi.org/project/redminelib/", timeout=10.0)
    print("  urllib3 explicit status:", r.status)
except Exception as exc:
    print("  urllib3 explicit failed:", type(exc).__name__, exc)

print("\nTesting requests explicit CA bundle:")
try:
    r = requests.get(
        "https://pypi.org/project/redminelib/", verify=CA_BUNDLE, timeout=10.0
    )
    print("  requests explicit status:", r.status_code)
except Exception as exc:
    print("  requests explicit failed:", type(exc).__name__, exc)
