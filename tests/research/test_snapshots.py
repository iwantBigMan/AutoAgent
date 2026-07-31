"""snapshots 결정론 층 테스트(스냅샷 저장·메타·되읽기).

Claude WebFetch 원문을 받아 runs/sources/*.txt로 고정하는 순수 코드라 pytest로 못박는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoagent.research.snapshots import (
    SourceSnapshot, load_snapshot_text, save_snapshot, slugify_ref, write_sources_manifest,
)


def test_slugify_ref_keeps_safe_ascii():
    assert slugify_ref("S1") == "s1"
    assert slugify_ref("src_2-a") == "src_2-a"


def test_slugify_ref_strips_path_traversal_and_nonascii():
    assert "/" not in slugify_ref("../etc/passwd")
    assert "\\" not in slugify_ref("a\\b")
    out = slugify_ref("회사::/../x")
    assert out and "/" not in out and "\\" not in out and ".." not in out


def test_slugify_ref_rejects_empty_result():
    with pytest.raises(ValueError):
        slugify_ref("///")


def test_save_snapshot_writes_file_and_computes_hash(tmp_path: Path):
    sources = tmp_path / "sources"
    snap = save_snapshot(sources, "S1", "https://example.com/a",
                         "Acme reported revenue of 12M in 2024.",
                         http_status=200, fetch_ts="2026-07-30T00:00:00Z")
    assert (sources / "s1.txt").read_text(encoding="utf-8") == "Acme reported revenue of 12M in 2024."
    assert snap.snapshot_path == "sources/s1.txt"
    assert snap.http_status == 200
    assert snap.char_count == len("Acme reported revenue of 12M in 2024.")
    assert len(snap.sha256) == 64
    assert load_snapshot_text(sources, "S1") == "Acme reported revenue of 12M in 2024."


def test_save_snapshot_default_fetch_ts_is_utc_iso(tmp_path: Path):
    snap = save_snapshot(tmp_path, "s2", "u", "body", http_status=200)
    assert snap.fetch_ts.endswith("Z") and "T" in snap.fetch_ts


def test_write_sources_manifest_roundtrips(tmp_path: Path):
    snaps = [
        save_snapshot(tmp_path / "src", "s1", "u1", "x", http_status=200, fetch_ts="2026-07-30T00:00:00Z"),
        save_snapshot(tmp_path / "src", "s2", "u2", "yy", http_status=404, fetch_ts="2026-07-30T00:00:00Z"),
    ]
    manifest = write_sources_manifest(tmp_path, snaps)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert [s["ref_id"] for s in data["sources"]] == ["s1", "s2"]
    assert data["sources"][1]["http_status"] == 404
