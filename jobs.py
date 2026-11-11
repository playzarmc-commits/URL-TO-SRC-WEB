"""In-memory job queue for long-running crawls.

Runs at most config.MAX_RUNNING_JOBS jobs at the same time; the rest wait in
a queue. Finished ZIPs live in a temp dir and are deleted after JOB_TTL.
The job table is per-process, so run the API with ONE gunicorn worker
(many threads) — see README / render.yaml.
"""

import os
import re
from collections import deque
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import config
import core

TMP_DIR = os.path.join(tempfile.gettempdir(), "url2html_jobs")
os.makedirs(TMP_DIR, exist_ok=True)

_LOCK = threading.Lock()
_JOBS: dict = {}
_RECENT = deque(maxlen=100)   # finished jobs, newest last (admin panel)
_EXECUTOR = ThreadPoolExecutor(max_workers=config.MAX_RUNNING_JOBS, thread_name_prefix="job")


class QueueFull(Exception):
    pass


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "download"


def _queue_position(job) -> int:
    """1 = next to run. 0 when not queued. Caller must hold _LOCK."""
    if job["status"] != "queued":
        return 0
    return 1 + sum(
        1 for j in _JOBS.values()
        if j["status"] == "queued" and j["created"] < job["created"]
    )


def _log_recent(job):
    """Caller holds _LOCK."""
    _RECENT.append({
        "time": job["finished"], "kind": job["kind"], "host": urlparse(job["url"]).netloc,
        "status": job["status"], "owner": job["owner_label"], "error": job["error"],
        "seconds": round(job["finished"] - (job["started"] or job["created"]), 1),
        "pages": (job["result"] or {}).get("pages"),
    })


def recent(limit=30):
    with _LOCK:
        return list(_RECENT)[::-1][:limit]


def _run(job_id: str):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started"] = time.time()

    def progress(pages=0, visited=0, message=""):
        with _LOCK:
            job["progress"] = {"pages": pages, "visited": visited, "message": message[:200]}

    try:
        if job["kind"] == "site":
            stats = core.crawl_full_site(job["url"], job["path"], progress_cb=progress)
            filename = _safe_name(f"{urlparse(job['url']).netloc}_full_site") + ".zip"
        else:  # "zip" — single page + assets
            resp = core.safe_get(job["url"], timeout=25)
            resp.raise_for_status()
            html = resp.text
            assets = core.build_offline_zip(html, job["url"], job["path"])
            stats = {"pages": 1, "assets": assets, "skipped": 0}
            filename = _safe_name(f"{core.extract_title(html)}_offline_package") + ".zip"
        size = os.path.getsize(job["path"])
        with _LOCK:
            job["result"] = {**stats, "size_bytes": size, "filename": filename}
            job["status"] = "done"
            job["finished"] = time.time()
            _log_recent(job)
    except Exception as exc:
        with _LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)[:300] or exc.__class__.__name__
            job["finished"] = time.time()
            _log_recent(job)
        try:
            os.remove(job["path"])
        except OSError:
            pass


def submit(owner_id: str, owner_label: str, kind: str, url: str) -> dict:
    with _LOCK:
        waiting = sum(1 for j in _JOBS.values() if j["status"] == "queued")
        if waiting >= config.MAX_QUEUED_JOBS:
            raise QueueFull()
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "owner": owner_id,
            "owner_label": owner_label,
            "kind": kind,
            "url": url,
            "status": "queued",
            "created": time.time(),
            "started": None,
            "finished": None,
            "progress": {"pages": 0, "visited": 0, "message": "Waiting in queue"},
            "result": None,
            "error": None,
            "path": os.path.join(TMP_DIR, f"{job_id}.zip"),
        }
        _JOBS[job_id] = job
        view = _view(job)
    _EXECUTOR.submit(_run, job_id)
    return view


def _view(job) -> dict:
    return {
        "job_id": job["id"],
        "kind": job["kind"],
        "url": job["url"],
        "status": job["status"],
        "queue_position": _queue_position(job),
        "progress": job["progress"],
        "result": job["result"],
        "error": job["error"],
    }


def get(job_id: str, owner_id: str):
    """Returns the job's public view, or None if unknown / not this owner's."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["owner"] != owner_id:
            return None
        return _view(job)


def file_for(job_id: str, owner_id: str):
    """Returns (path, filename) for a finished job, else None."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job["owner"] != owner_id or job["status"] != "done":
            return None
        if not os.path.exists(job["path"]):
            return None
        return job["path"], job["result"]["filename"]


def stats() -> dict:
    with _LOCK:
        counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        for j in _JOBS.values():
            counts[j["status"]] += 1
        return counts


def _cleanup_loop():
    while True:
        time.sleep(60)
        cutoff = time.time() - config.JOB_TTL_SECONDS
        with _LOCK:
            expired = [
                jid for jid, j in _JOBS.items()
                if j["finished"] and j["finished"] < cutoff
            ]
            for jid in expired:
                path = _JOBS.pop(jid)["path"]
                try:
                    os.remove(path)
                except OSError:
                    pass


threading.Thread(target=_cleanup_loop, daemon=True).start()
