"""Command line entry point: ``python -m mip.cli <command>``."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import get_settings
from .db import init_db, session_scope


def cmd_init_db(_):
    init_db()
    print("database initialised")


def cmd_import(args):
    from .taxonomy.importer import TaxonomyImporter

    init_db()
    root = args.root or get_settings().data_root
    t = time.time()
    with session_scope() as s:
        stats = TaxonomyImporter(s, root, force=args.force).run()
        import datetime as dt
        from sqlalchemy import select
        from .models import Source
        src = s.execute(select(Source).where(Source.slug == "emithran-taxonomy")).scalar_one_or_none()
        if src:
            src.last_run_at = src.last_success_at = dt.datetime.now(dt.timezone.utc)
    print(json.dumps({"root": str(root), "seconds": round(time.time() - t, 1), **stats}, indent=2))


def cmd_seed_sources(args):
    from .registry.service import seed_sources

    init_db()
    with session_scope() as s:
        res = seed_sources(s, update_existing=args.update)
    print(json.dumps(res, indent=2))


def cmd_crawl(args):
    """Queue + run one source synchronously (useful for debugging)."""
    from .jobs.service import create_job
    from .crawler.run import run_job

    init_db()
    with session_scope() as s:
        job = create_job(s, args.source, trigger="manual", params=json.loads(args.params or "{}"))
        job_id = job.id
    code = run_job(job_id)
    with session_scope() as s:
        from .models import CrawlJob
        j = s.get(CrawlJob, job_id)
        print(json.dumps({"job": job_id, "status": j.status, "stats": j.stats, "error": j.error}, indent=2, default=str))
    sys.exit(code)


def cmd_create_key(args):
    """Bootstrap an API key without the console (prints the secret once)."""
    import datetime as dt

    from .models import ApiKey
    from .publicapi.auth import READ_SCOPES, SCOPES, new_api_key, sha256

    init_db()
    scopes = args.scopes.split(",") if args.scopes else READ_SCOPES
    bad = [x for x in scopes if x not in SCOPES]
    if bad:
        sys.exit(f"unknown scopes: {bad}; available: {list(SCOPES)}")
    prefix, key = new_api_key()
    with session_scope() as s:
        s.add(ApiKey(name=args.name, prefix=prefix, key_hash=sha256(key), scopes=scopes,
                     expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=args.days) if args.days else None))
    print(json.dumps({"apiKey": key, "scopes": scopes, "note": "shown once — store it securely"}, indent=2))


def cmd_worker(_):
    from .worker import run_forever

    init_db()
    run_forever()


def cmd_serve(args):
    import uvicorn

    init_db()
    uvicorn.run("mip.api.main:app", host=args.host, port=args.port, reload=False, log_level="info")


def cmd_tag_news(_):
    from .news.tagging import retag_all

    with session_scope() as s:
        print(json.dumps(retag_all(s), indent=2))


def cmd_evaluate(_):
    from .intelligence.alerts import evaluate_alerts
    from .registry.service import refresh_freshness

    with session_scope() as s:
        print(json.dumps({"freshness": refresh_freshness(s), "alerts": evaluate_alerts(s)}, indent=2, default=str))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="mip")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sp = sub.add_parser("import-taxonomy", help="import the existing memory/ folder")
    sp.add_argument("--root")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_import)
    sp = sub.add_parser("seed-sources")
    sp.add_argument("--update", action="store_true", help="overwrite config of existing sources from YAML")
    sp.set_defaults(fn=cmd_seed_sources)
    sp = sub.add_parser("crawl")
    sp.add_argument("source")
    sp.add_argument("--params")
    sp.set_defaults(fn=cmd_crawl)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sp = sub.add_parser("create-api-key", help="create a public API v2 key")
    sp.add_argument("name")
    sp.add_argument("--scopes", help="comma-separated; default all read scopes")
    sp.add_argument("--days", type=int, default=90)
    sp.set_defaults(fn=cmd_create_key)
    sp = sub.add_parser("serve")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(fn=cmd_serve)
    sub.add_parser("tag-news", help="re-tag stored news from topics.yaml").set_defaults(fn=cmd_tag_news)
    sub.add_parser("evaluate").set_defaults(fn=cmd_evaluate)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
