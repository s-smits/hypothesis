import uuid
from pathlib import Path


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, making its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so a reader never sees half a file.
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
