"""Local BGE embeddings shared by indexing and search."""

import math
import threading
from collections.abc import Iterable
from functools import lru_cache

from filemind.config import get_settings

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
_model_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_model() -> object:
    from fastembed import TextEmbedding
    from huggingface_hub import snapshot_download

    settings = get_settings()
    revision = settings.embedding_model_revision
    model_dir = (
        settings.model_cache_dir
        / "huggingface"
        / "models--Qdrant--bge-small-zh-v1.5"
        / "snapshots"
        / revision
    )
    required_files = ("model_optimized.onnx", "tokenizer.json", "config.json")
    if not all((model_dir / name).is_file() for name in required_files):
        snapshot_download(
            repo_id="Qdrant/bge-small-zh-v1.5",
            revision=revision,
            cache_dir=str(settings.model_cache_dir / "huggingface"),
        )
    return TextEmbedding(
        model_name=settings.embedding_model,
        cache_dir=str(settings.model_cache_dir),
        threads=1,
        providers=["CPUExecutionProvider"],
        specific_model_path=str(model_dir),
    )


def embed_texts(texts: Iterable[str], *, query: bool = False) -> list[list[float]]:
    """Return normalized vectors; model setup/download failures are surfaced to callers."""
    values = list(texts)
    if not values:
        return []
    from fastembed import TextEmbedding

    with _model_lock:
        model = _load_model()
        if not isinstance(model, TextEmbedding):
            raise RuntimeError("embedding model initialization failed")
        prefix = QUERY_INSTRUCTION if query else ""
        vectors = list(model.embed([prefix + value for value in values], batch_size=32))
    normalized: list[list[float]] = []
    expected_dim = get_settings().embedding_dim
    for vector in vectors:
        values_float = [float(item) for item in vector.tolist()]
        if len(values_float) != expected_dim:
            raise RuntimeError("embedding dimension mismatch")
        norm = math.sqrt(sum(value * value for value in values_float))
        if norm == 0:
            raise RuntimeError("embedding model returned a zero vector")
        normalized.append([value / norm for value in values_float])
    return normalized
