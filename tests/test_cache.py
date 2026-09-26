"""Cache tests: gh_api caches successful responses for an hour."""

import json

import pytest

from taken import checks


class Proc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture
def cache_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKEN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(checks, "_CACHE_ENABLED", True)


@pytest.fixture
def counting_run(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        calls.append(cmd)
        return Proc(json.dumps({"ok": True, "n_calls": len(calls)}))

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    return calls


def test_second_identical_call_uses_cache(cache_env, counting_run):
    first = checks.gh_api("repos/octo/repo")
    second = checks.gh_api("repos/octo/repo")
    assert len(counting_run) == 1
    assert first == second == {"ok": True, "n_calls": 1}


def test_params_are_part_of_cache_key(cache_env, counting_run):
    checks.gh_api("repos/octo/repo/issues/1/comments", {"per_page": "100"})
    checks.gh_api("repos/octo/repo/issues/1/comments", {"per_page": "50"})
    assert len(counting_run) == 2


def test_stale_entry_refetches(cache_env, counting_run, tmp_path):
    checks.gh_api("repos/octo/repo")
    assert len(counting_run) == 1
    cache_file = tmp_path / "cache" / "api_cache.json"
    entries = json.loads(cache_file.read_text())
    key = next(iter(entries))
    entries[key]["fetched_at"] -= checks.CACHE_TTL_SECONDS + 1
    cache_file.write_text(json.dumps(entries))
    checks.gh_api("repos/octo/repo")
    assert len(counting_run) == 2


def test_cache_disabled_refetches_every_time(cache_env, counting_run, monkeypatch):
    monkeypatch.setattr(checks, "_CACHE_ENABLED", False)
    checks.gh_api("repos/octo/repo")
    checks.gh_api("repos/octo/repo")
    assert len(counting_run) == 2


def test_corrupt_cache_file_is_ignored(cache_env, counting_run, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "api_cache.json").write_text("not json{{{")
    assert checks.gh_api("repos/octo/repo") == {"ok": True, "n_calls": 1}
    assert len(counting_run) == 1


def test_unwritable_cache_dir_does_not_break(cache_env, counting_run, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a dir")
    import os

    os.environ["TAKEN_CACHE_DIR"] = str(blocker)
    assert checks.gh_api("repos/octo/repo") == {"ok": True, "n_calls": 1}
    assert len(counting_run) == 1


def test_no_cache_flag_disables_cache(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TAKEN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(checks, "_CACHE_ENABLED", True)  # restored on teardown
    calls = []

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        calls.append(cmd)
        return Proc("not used")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    # --no-cache with an unparseable target: must exit 3 before any API call
    from taken.cli import main

    assert main(["--no-cache", "bogus"]) == 3
    assert checks._CACHE_ENABLED is False
    assert calls == []


def test_concurrent_writes_keep_cache_valid(cache_env, counting_run):
    import threading

    errors = []

    def worker(n):
        try:
            for i in range(10):
                checks.gh_api(f"repos/octo/repo{n}", {"page": str(i)})
        except Exception as exc:  # noqa: BLE001 - any failure here is the bug
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # Every key written by every thread must be present and readable.
    for n in range(8):
        for i in range(10):
            data = checks.gh_api(f"repos/octo/repo{n}", {"page": str(i)})
            assert data["ok"] is True
