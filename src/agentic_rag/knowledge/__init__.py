"""Knowledge ingestion and dense retrieval infrastructure."""

from .dense import DenseRetrievalResult, DenseRetrievalService
from .embedding import EmbeddingClient, EmbeddingError

__all__ = [
    "DenseRetrievalResult",
    "DenseRetrievalService",
    "EmbeddingClient",
    "EmbeddingError",
]
