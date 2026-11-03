"""Focused validation tests for the internal Batch submission API."""

import importlib.util
from pathlib import Path

import pytest


API_PATH = Path(__file__).with_name("api.py")
SPEC = importlib.util.spec_from_file_location("batch_api", str(API_PATH))
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def valid_payload():
    return {
        "analysis_type": "Nanopore",
        "command_file": ["foodport-nanopore-task --manifest '$MANIFEST'"],
        "container": "nanopore-runs",
        "download_file_pattern": [],
        "input_file_pattern": [
            "nanopore-runs/42/input/manifest.json",
            "nanopore-runs/42/input/pod5/",
        ],
        "vm_size": "Standard_NV18ads_A10_v5",
        "output_file_pattern": ["output/"],
        "output_container": "nanopore-results",
        "output_prefix": "runs/260825-nanopore",
        "unique_id": "foodport-nanopore-42",
        "log_prefix": "260825-nanopore/logs",
    }


def test_nanopore_payload_normalizes_patterns():
    payload = api._validate_payload(valid_payload())
    assert payload["analysis_type"] == "Nanopore"
    assert payload["input_patterns"] == [
        "nanopore-runs/42/input/manifest.json",
        "nanopore-runs/42/input/pod5/",
    ]
    assert payload["download_patterns"] == []
    assert payload["output_patterns"] == ["output/"]
    assert payload["output_container"] == "nanopore-results"
    assert payload["output_prefix"] == "runs/260825-nanopore"


def test_nanopore_payload_rejects_scalar_input_pattern():
    payload = valid_payload()
    payload["input_file_pattern"] = "nanopore-runs/42/input/**"
    with pytest.raises(TypeError):
        api._validate_payload(payload)
