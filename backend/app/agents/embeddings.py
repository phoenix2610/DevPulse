import hashlib
import math
import re

from ..config import get_settings

_TOKEN = re.compile(r"[a-z0-9_]+")


def embed(text: str) -> list[float]:
    dim = get_settings().embedding_dim
    vec = [0.0] * dim
    tokens = _TOKEN.findall(text.lower())
    for tok in tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
