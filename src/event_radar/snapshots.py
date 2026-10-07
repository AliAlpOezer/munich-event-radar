import gzip
import hashlib
from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.gz"

    def put(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(body))
            tmp.replace(path)
        return digest

    def get(self, digest: str) -> bytes:
        return gzip.decompress(self._path(digest).read_bytes())

    def has(self, digest: str) -> bool:
        return self._path(digest).exists()
