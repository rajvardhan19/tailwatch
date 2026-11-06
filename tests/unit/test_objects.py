from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tailwatch.sources.objects import LocalObjectStore


def test_atomic_deduplication_and_checksum(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = list(pool.map(store.put, [b"synthetic evidence"] * 20))
    assert len(set(digests)) == 1
    assert store.get(digests[0]) == b"synthetic evidence"
    assert len(list(tmp_path.rglob("*"))) == 2  # one prefix directory, one object
    (tmp_path / digests[0][:2] / digests[0]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        store.get(digests[0])
    with pytest.raises(ValueError, match="checksum"):
        store.put(b"synthetic evidence")


def test_object_keys_cannot_escape_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalObjectStore(tmp_path).get("../../secrets")
