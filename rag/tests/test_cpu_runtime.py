"""Runtime dependency constraints for the RAG service."""

import torch


def test_torch_build_is_cpu_only() -> None:
    """The RAG image must not include CUDA-enabled PyTorch."""
    assert torch.version.cuda is None
