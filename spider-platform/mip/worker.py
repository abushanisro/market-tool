"""Scheduler + dispatcher.

* scheduler tick: enqueue due sources (interval or cron, UTC) — at most one queued/running job per source
* dispatcher tick: claim queued jobs (SKIP LOCKED) up to ``max_parallel_jobs`` and run each in its own
  OS process (``python -m mip.crawler.run <job_id>``); reap finished processes; enforce timeouts
* recovery tick: fail jobs whose heartbeat stopped (crashed worker) so they never stay "running"
* evaluation tick: alerts + freshness

With Redis configured, a lock guarantees a single active scheduler across replicas; job events are
published on the ``mip:jobs`` channel. Without Redis the worker still runs (single instance).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .jobs.service import JobError, claim_jobs, create_job, recover_stale
from .models import CrawlJob, Source
from .registry.service import RUNNABLE_VERIFICATION

log = logging.getLogger("mip.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class _Lock:
    def __init__(self):
        self.r = None
        url = get_settings().redis_url
        if url:
            try:
                import redis

                self.r = redis.Redis.from_url(url)
                self.r.ping()
            except Exception as exc:  # optional dependency: degrade gracefully
                log.warning("redis unavailable (%s) — running without distributed lock", exc)
                self.r = None

    def acquire(self, name: str, ttl: int) -> bool:
        if self.r is None:
            return True
        return bool(self.r.set(f"mip:lock:{name}", WORKER_ID, nx=True, ex=ttl)) or \
            (self.r.get(f"mip:lock:{name}") or b"").decode() == WORKER_ID

    def publish(self, payload: str) -> None:
        if self.r is not None:
            try:
                self.r.publish("mip:jobs", payload)
            except Exception:
                pass


def is_due(src: Source, now: dt.datetime) -> bool:
    last = src.last_run_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    if src.schedule_cron:
        trig = CronTrigger.from_crontab(src.schedule_cron, timezone="UTC")
        nxt = trig.get_next_fire_time(None, last or (now - dt.timedelta(days=366)))
        return nxt is not None and nxt <= now
    if src.schedule_interval_minutes:
        return last is None or (now - last).total_seconds() >= src.schedule_interval_minutes * 60
    return False


class Worker:
    def __init__(self):
        self.cfg = get_settings()
        self.lock = _Lock()
        self.procs: dict[int, tuple[subprocess.Popen, float]] = {}
        self.mutex = threading.Lock()
        self.sched = BackgroundScheduler(timezone="UTC")

    # ------------------------------------------------------------------ ticks
    def schedule_tick(self) -> None:
        if not self.lock.acquire("scheduler", self.cfg.scheduler_tick_seconds * 3):
            return
        now = dt.datetime.now(dt.timezone.utc)
        with session_scope() as s:
            sources = s.execute(select(Source).where(Source.status == "active",
                                                     Source.verification_status.in_(RUNNABLE_VERIFICATION),
                                                     Source.source_type != "internal")).scalars().all()
            for src in sources:
                if not is_due(src, now):
                    continue
                try:
                    with s.begin_nested():
                        job = create_job(s, src.id, trigger="schedule")
                    log.info("scheduled job %s for %s", job.id, src.slug)
                except JobError as exc:
                    log.debug("skip %s: %s", src.slug, exc)

    def dispatch_tick(self) -> None:
        with self.mutex:
            self._reap()
            free = self._setting("max_parallel_jobs", self.cfg.max_parallel_jobs) - len(self.procs)
            if free <= 0:
                return
            with session_scope() as s:
                jobs = claim_jobs(s, free, WORKER_ID)
                ids = [j.id for j in jobs]
            for jid in ids:
                p = subprocess.Popen([sys.executable, "-m", "mip.crawler.run", str(jid)],
                                     stdout=open(self.cfg.jobs_dir / f"job_{jid}.log", "ab"), stderr=subprocess.STDOUT,
                                     env={**os.environ}, start_new_session=True)
                self.procs[jid] = (p, time.time())
                self.lock.publish(f'{{"job": {jid}, "event": "started"}}')
                log.info("started job %s pid=%s", jid, p.pid)

    def _setting(self, key: str, default):
        """Operator overrides saved from the Settings screen (app_settings table)."""
        from .models import AppSetting

        with session_scope() as s:
            row = s.get(AppSetting, key)
            return type(default)(row.value) if row is not None else default

    def _reap(self) -> None:
        timeout = self._setting("job_timeout_minutes", self.cfg.job_timeout_minutes) * 60 + 120
        for jid, (p, started) in list(self.procs.items()):
            rc = p.poll()
            if rc is None and time.time() - started > timeout:
                log.warning("job %s exceeded timeout — terminating", jid)
                os.killpg(p.pid, signal.SIGTERM)
                continue
            if rc is not None:
                del self.procs[jid]
                self.lock.publish(f'{{"job": {jid}, "event": "finished", "rc": {rc}}}')
                with session_scope() as s:
                    j = s.get(CrawlJob, jid)
                    if j and j.status in ("starting", "running"):  # process died before finalize()
                        j.status, j.error = "failed", f"process exited with code {rc} before finalizing"
                        j.finished_at = dt.datetime.now(dt.timezone.utc)

    def recovery_tick(self) -> None:
        with session_scope() as s:
            n = recover_stale(s, self.cfg.stale_heartbeat_seconds)
            if n:
                log.warning("marked %s stale job(s) as failed", n)

    def evaluation_tick(self) -> None:
        from .intelligence.alerts import evaluate_alerts

        with session_scope() as s:
            evaluate_alerts(s)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.sched.add_job(self.schedule_tick, "interval", seconds=self.cfg.scheduler_tick_seconds, id="schedule",
                           max_instances=1, coalesce=True, next_run_time=dt.datetime.now(dt.timezone.utc))
        self.sched.add_job(self.dispatch_tick, "interval", seconds=self.cfg.dispatcher_tick_seconds, id="dispatch",
                           max_instances=1, coalesce=True)
        self.sched.add_job(self.recovery_tick, "interval", seconds=60, id="recover", max_instances=1, coalesce=True)
        self.sched.add_job(self.evaluation_tick, "interval", minutes=15, id="evaluate", max_instances=1, coalesce=True)
        self.sched.start()
        log.info("worker %s started (max_parallel_jobs=%s)", WORKER_ID, self.cfg.max_parallel_jobs)

    def stop(self) -> None:
        self.sched.shutdown(wait=False)
        for jid, (p, _) in self.procs.items():
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def run_forever() -> None:
    w = Worker()
    w.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        stop.wait(1)
    w.stop()
