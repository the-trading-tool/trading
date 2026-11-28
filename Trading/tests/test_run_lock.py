"""Run locks: one writer per simulation DB, released when the holder dies."""
import os
import subprocess
import sys
import time

from tradinglib.run_lock import RunLock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOLD = """
import sys, time
sys.path.insert(0, {root!r})
from tradinglib.run_lock import RunLock
lock = RunLock({path!r}, 'holder')
assert lock.acquire(blocking=False)
print('locked', flush=True)
time.sleep(60)
"""


def _holder(path):
    p = subprocess.Popen([sys.executable, "-c", HOLD.format(root=ROOT, path=str(path))],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "locked"
    return p


def test_second_process_cannot_take_the_lock(tmp_path):
    path = tmp_path / "perf2_x.lock"
    p = _holder(path)
    try:
        other = RunLock(str(path))
        assert other.acquire(blocking=False) is False
    finally:
        p.kill()
        p.wait()


def test_lock_is_free_after_the_holder_is_killed(tmp_path):
    path = tmp_path / "perf2_x.lock"
    p = _holder(path)
    p.kill()
    p.wait()
    lock = RunLock(str(path), "me")
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_blocking_acquire_waits_for_release(tmp_path):
    path = tmp_path / "perf2_x.lock"
    p = _holder(path)
    waited = []
    try:
        lock = RunLock(str(path))
        t0 = time.time()
        killer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        killer.wait()
        p.kill()
        p.wait()
        assert lock.acquire(blocking=True, poll=0.2, on_wait=waited.append) is True
        lock.release()
        assert time.time() - t0 >= 1.5
    finally:
        if p.poll() is None:
            p.kill()


def test_release_and_reacquire_in_process(tmp_path):
    lock = RunLock(str(tmp_path / "a.lock"), "one")
    assert lock.acquire(blocking=False)
    lock.release()
    again = RunLock(str(tmp_path / "a.lock"), "two")
    assert again.acquire(blocking=False)
    again.release()


def test_asset_perf2_skips_when_its_db_is_taken(tmp_path):
    lock_path = tmp_path / "perf2_asset_simulation_.lock"
    p = _holder(lock_path)
    try:
        env = dict(os.environ, TradingDB=str(tmp_path))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "asset_perf2.py"), "/silent"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    finally:
        p.kill()
        p.wait()
    assert r.returncode == 0
    assert "skipping this run" in (r.stdout + r.stderr)
    # nothing was created besides the lock file
    assert not (tmp_path / "asset_simulation_.db").exists()
