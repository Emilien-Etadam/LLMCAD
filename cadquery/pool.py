"""
@file pool.py
@brief Pool de workers Python persistants (stdin/stdout JSON ligne unique).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_WORKER = _HERE / "worker.py"
_PYTHON = sys.executable


@dataclass
class _WorkerHandle:
    proc: subprocess.Popen[str]


class WorkerPool:
    """
    Maintient `size` processus `worker.py --persistent`. Les requêtes
    bloquent sur acquire si tous les workers sont occupés (pas de file
    de requêtes — acceptable en usage mono-utilisateur).
    """

    def __init__(self, size: int = 2, mem_limit_mb: int = 4096) -> None:
        self._size = max(1, size)
        self._mem_limit_mb = mem_limit_mb
        self._idle: queue.Queue[_WorkerHandle] = queue.Queue()
        self._workers: list[_WorkerHandle] = []
        self._workers_lock = threading.Lock()
        self._started = False
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
        print(
            f"[pool] starting {self._size} persistent workers "
            f"(mem_limit_mb={self._mem_limit_mb})",
            flush=True,
        )
        ws = [self._spawn_worker() for _ in range(self._size)]
        with self._workers_lock:
            self._workers = ws
        for w in ws:
            self._idle.put(w)

    def _spawn_worker(self) -> _WorkerHandle:
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "CADQUERY_WORKER_MEM_LIMIT_MB": str(self._mem_limit_mb),
        }
        proc = subprocess.Popen(
            [_PYTHON, str(_WORKER), "--persistent"],
            cwd=str(_HERE),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        print(f"[pool] worker born pid={proc.pid}", flush=True)

        def drain_stderr() -> None:
            if proc.stderr is None:
                return
            try:
                for line in iter(proc.stderr.readline, ""):
                    if line:
                        print(f"[worker {proc.pid} stderr] {line}", end="", flush=True)
            except (BrokenPipeError, ValueError):
                pass

        threading.Thread(target=drain_stderr, daemon=True).start()
        return _WorkerHandle(proc=proc)

    def _terminate_worker(self, w: _WorkerHandle, grace_s: float = 5.0) -> None:
        pid = w.proc.pid
        if w.proc.poll() is not None:
            print(f"[pool] worker already dead pid={pid}", flush=True)
            return
        try:
            w.proc.terminate()
        except OSError:
            pass
        try:
            w.proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            try:
                w.proc.kill()
            except OSError:
                pass
            try:
                w.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        print(f"[pool] worker terminated pid={pid}", flush=True)

    def _replace_worker(self, dead: _WorkerHandle) -> _WorkerHandle:
        """Remplace `dead` dans le registre du pool par un processus neuf."""
        self._terminate_worker(dead)
        fresh = self._spawn_worker()
        with self._workers_lock:
            replaced = False
            for i, wh in enumerate(self._workers):
                if wh is dead:
                    self._workers[i] = fresh
                    replaced = True
                    break
            if not replaced:
                self._workers.append(fresh)
        return fresh

    def shutdown(self) -> None:
        with self._start_lock:
            if not self._started:
                return
            self._started = False

        with self._workers_lock:
            live = list(self._workers)
            self._workers.clear()

        print("[pool] shutdown: terminating workers", flush=True)
        for w in live:
            self._terminate_worker(w)

        while True:
            try:
                self._idle.get_nowait()
            except queue.Empty:
                break
        print("[pool] shutdown complete", flush=True)

    def workers_total(self) -> int:
        return self._size

    def workers_alive(self) -> int:
        with self._workers_lock:
            snap = list(self._workers)
        return sum(1 for w in snap if w.proc.poll() is None)

    def execute(self, op: str, code: str, timeout: float = 30.0) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("WorkerPool.start() required")

        w = self._idle.get()
        if w.proc.poll() is not None:
            print(
                f"[pool] worker dead on acquire pid={w.proc.pid}, replacing",
                flush=True,
            )
            w = self._replace_worker(w)

        try:
            if w.proc.stdin is None or w.proc.stdout is None:
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
                return {
                    "ok": False,
                    "error": "Worker pipes not available",
                    "traceback": "",
                }

            req: dict[str, Any] = {"op": op, "code": code}
            payload = json.dumps(req, ensure_ascii=False, separators=(",", ":"))
            w.proc.stdin.write(payload + "\n")
            w.proc.stdin.flush()

            result_line: list[str | None] = [None]

            def read_one() -> None:
                try:
                    result_line[0] = w.proc.stdout.readline()
                except BrokenPipeError:
                    result_line[0] = ""

            reader = threading.Thread(target=read_one, daemon=True)
            reader.start()
            reader.join(timeout)

            if reader.is_alive():
                print(
                    f"[pool] timeout {timeout:g}s, killing worker pid={w.proc.pid}",
                    flush=True,
                )
                try:
                    w.proc.kill()
                except OSError:
                    pass
                try:
                    w.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
                return {
                    "ok": False,
                    "error": (
                        f"Execution timeout exceeded ({timeout:g}s). "
                        "Possible infinite loop or runaway computation."
                    ),
                    "traceback": "",
                }

            raw = (result_line[0] or "").strip()
            crashed = w.proc.poll() is not None

            if crashed and not raw:
                print(
                    f"[pool] worker crashed pid={w.proc.pid}, replacing",
                    flush=True,
                )
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
                return {
                    "ok": False,
                    "error": "Worker process died (crash or EOF).",
                    "traceback": "",
                }

            if not raw:
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
                return {
                    "ok": False,
                    "error": "Empty response from worker",
                    "traceback": "",
                }

            try:
                out = json.loads(raw)
            except json.JSONDecodeError as exc:
                if crashed:
                    fresh = self._replace_worker(w)
                    self._idle.put(fresh)
                else:
                    self._idle.put(w)
                return {
                    "ok": False,
                    "error": f"Invalid JSON from worker: {exc}",
                    "traceback": raw[:500],
                }

            if not isinstance(out, dict):
                if crashed:
                    fresh = self._replace_worker(w)
                    self._idle.put(fresh)
                else:
                    self._idle.put(w)
                return {"ok": False, "error": "Worker response not an object", "traceback": ""}

            if crashed:
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
            else:
                self._idle.put(w)
            return out
        except BaseException:
            try:
                fresh = self._replace_worker(w)
                self._idle.put(fresh)
            except Exception:
                pass
            raise
