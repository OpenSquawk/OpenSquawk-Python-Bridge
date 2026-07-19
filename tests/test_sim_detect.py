import subprocess

import sim_detect


def _fake_run(returncode=0, stdout=""):
    def run(*a, **k):
        return subprocess.CompletedProcess(a[0], returncode, stdout, "")
    return run


def test_process_running_caches(monkeypatch):
    sim_detect._CACHE.clear()
    calls = {"n": 0}

    def run(*a, **k):
        calls["n"] += 1
        return subprocess.CompletedProcess(a[0], 0, "match\n", "")

    monkeypatch.setattr(sim_detect.sys, "platform", "darwin")
    monkeypatch.setattr(sim_detect.subprocess, "run", run)
    assert sim_detect.process_running(["fgfs"]) is True
    assert sim_detect.process_running(["fgfs"]) is True  # served from cache
    assert calls["n"] == 1


def test_process_running_false_when_absent(monkeypatch):
    sim_detect._CACHE.clear()
    monkeypatch.setattr(sim_detect.sys, "platform", "darwin")
    monkeypatch.setattr(sim_detect.subprocess, "run", _fake_run(returncode=1, stdout=""))
    assert sim_detect.process_running(["nope"]) is False


def test_process_running_windows_substring(monkeypatch):
    sim_detect._CACHE.clear()
    monkeypatch.setattr(sim_detect.sys, "platform", "win32")
    monkeypatch.setattr(
        sim_detect.subprocess, "run",
        _fake_run(stdout="FlightSimulator2024.exe  1234 Console\n"),
    )
    assert sim_detect.process_running(["FlightSimulator2024.exe"]) is True
    sim_detect._CACHE.clear()
    assert sim_detect.process_running(["fgfs"]) is False


def test_process_running_swallows_errors(monkeypatch):
    sim_detect._CACHE.clear()

    def boom(*a, **k):
        raise OSError("no such tool")

    monkeypatch.setattr(sim_detect.subprocess, "run", boom)
    assert sim_detect.process_running(["x"]) is False
