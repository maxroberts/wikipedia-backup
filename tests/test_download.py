"""Tests for ``wikibackup.download``: resumable download rules, hashing, verified copy.

Downloads go to the local ``file_server`` fixture (``conftest.py``); ``sleep`` is replaced
so retries don't wait. Files are a few hundred KB of random bytes, larger than one socket
buffer, so a dropped connection really does leave a partial file.
"""

import hashlib
import os

import pytest

from wikibackup import download

DATA = os.urandom(300_000)
SHA = hashlib.sha256(DATA).hexdigest()


def no_sleep(seconds):
    """Stand-in for time.sleep that records nothing and returns at once."""


def test_fresh_download(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA)
    part = tmp_path / "partial" / "f.zim.part"
    download.download_resumable([url], part, len(DATA), sleep=no_sleep)
    assert part.read_bytes() == DATA
    assert file_server.requests == [("/f.zim", "bytes=0-")]


def test_resumes_from_existing_part(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA)
    part = tmp_path / "f.zim.part"
    part.write_bytes(DATA[:100_000])
    download.download_resumable([url], part, len(DATA), sleep=no_sleep)
    assert part.read_bytes() == DATA
    assert file_server.requests == [("/f.zim", "bytes=100000-")]


def test_dropped_connection_is_resumed(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA, mode="drop-once")
    part = tmp_path / "f.zim.part"
    sleeps = []
    download.download_resumable([url], part, len(DATA), sleep=sleeps.append)
    assert part.read_bytes() == DATA
    assert len(file_server.requests) == 2
    second_range = file_server.requests[1][1]
    assert second_range != "bytes=0-"  # it resumed rather than starting over
    assert sleeps == [2.0]


def test_server_ignoring_range_restarts_from_zero(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA, mode="no-range")
    part = tmp_path / "f.zim.part"
    part.write_bytes(DATA[:100_000])
    download.download_resumable([url], part, len(DATA), sleep=no_sleep)
    assert part.read_bytes() == DATA


def test_falls_back_to_next_mirror(file_server, tmp_path):
    good = file_server.add("/good.zim", DATA)
    part = tmp_path / "f.zim.part"
    download.download_resumable([file_server.url + "/missing.zim", good], part, len(DATA),
                                sleep=no_sleep)
    assert part.read_bytes() == DATA
    assert [p for p, _ in file_server.requests] == ["/missing.zim", "/good.zim"]


def test_gives_up_and_keeps_part(file_server, tmp_path):
    part = tmp_path / "f.zim.part"
    part.write_bytes(b"x" * 10)
    sleeps = []
    with pytest.raises(download.DownloadError, match="gave up after 4 attempts"):
        download.download_resumable([file_server.url + "/missing.zim"], part, len(DATA),
                                    max_attempts=4, sleep=sleeps.append)
    assert part.read_bytes() == b"x" * 10
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


def test_wrong_content_range_is_rejected(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA, mode="wrong-total")
    with pytest.raises(download.DownloadError, match="Content-Range"):
        download.download_resumable([url], tmp_path / "f.part", len(DATA), max_attempts=1,
                                    sleep=no_sleep)


def test_oversized_part_restarts(file_server, tmp_path):
    url = file_server.add("/f.zim", DATA)
    part = tmp_path / "f.zim.part"
    part.write_bytes(DATA + b"extra")
    download.download_resumable([url], part, len(DATA), sleep=no_sleep)
    assert part.read_bytes() == DATA


def test_complete_part_needs_no_request(file_server, tmp_path):
    part = tmp_path / "f.zim.part"
    part.write_bytes(DATA)
    download.download_resumable([file_server.url + "/f.zim"], part, len(DATA), sleep=no_sleep)
    assert file_server.requests == []


def test_sha256_file(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(DATA)
    assert download.sha256_file(path) == SHA


def test_copy_verified(tmp_path):
    src = tmp_path / "a" / "w_2026-08.zim"
    src.parent.mkdir()
    src.write_bytes(DATA)
    final = download.copy_verified(src, tmp_path / "hot", SHA)
    assert final.read_bytes() == DATA
    assert (tmp_path / "hot" / "w_2026-08.zim.sha256").read_text() == f"{SHA}  w_2026-08.zim\n"
    assert sorted(p.name for p in (tmp_path / "hot").iterdir()) == [
        "w_2026-08.zim", "w_2026-08.zim.sha256",
    ]


def test_copy_verified_bad_hash_leaves_nothing(tmp_path):
    src = tmp_path / "w_2026-08.zim"
    src.write_bytes(DATA)
    with pytest.raises(download.VerifyError):
        download.copy_verified(src, tmp_path / "hot", "0" * 64)
    assert list((tmp_path / "hot").iterdir()) == []
