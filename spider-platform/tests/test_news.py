"""Manufacturing news: classifier, query generators, collectors (via the control plane), feed API, hotspots and agent history."""
import base64
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

NOW = dt.datetime(2026, 10, 27, 16, 40, tzinfo=dt.timezone.utc).isoformat()


# ------------------------------------------------------------------------------------------ classifier
def test_classifier_tags_category_country_location_and_rejects_noise():
    from emithran.news.classify import classify

    t = classify("Tata plans new die casting plant in Pune as India auto output rises", publisher_country="IND")
    assert t.relevant and {"cat:die-casting", "country:IND", "loc:Pune", "pub:IND"} <= set(t.tags)
    assert "die casting" in t.why["cat:die-casting"][0]
    t = classify("Ohio forging plant to add 200 jobs, closed-die forging capacity expansion")
    assert {"cat:forging", "country:USA", "loc:Ohio"} <= set(t.tags)
    t = classify("Shenzhen PCB assembly makers face tariff pressure")
    assert {"cat:pcba", "country:CHN", "loc:Shenzhen"} <= set(t.tags)
    for noise in ["Casting call for new Marvel film announced", "Forging alliances: EU and Japan sign trade pact",
                  "12 Classic Tech Inventions From The 1960s That Shaped The Modern World"]:
        assert not classify(noise).relevant, noise                     # ambiguous words need manufacturing context
    assert classify("Quarterly results beat expectations", trusted=True).relevant      # a manufacturing publication's own feed
    assert classify("Vendor opens new factory", publisher_country="MEX").tags.count("country:MEX") == 1   # publisher is the only country signal


def test_every_taxonomy_category_has_a_news_topic():
    from emithran.db import session_scope
    from emithran.models import Category
    from emithran.news.classify import load_topics

    reference = {"countries-data", "database", "standards"}
    topics = set(load_topics()["cats"])
    with session_scope() as s:
        # categories that exist only after a real import are checked in the live smoke test; fixtures cover the mini taxonomy
        slugs = {c.slug for c in s.execute(select(Category)).scalars()}
    assert not (slugs - reference - topics - {"unmapped"}), f"categories without a news topic: {slugs - reference - topics}"
    assert {"die-casting", "forging", "sheet-metal", "machining", "pcba", "plastic-molding", "additive-manufacturing"} <= topics


# ------------------------------------------------------------------------------------------ query generators
def test_gdelt_and_hn_query_generators():
    from urllib.parse import parse_qs, urlparse

    from emithran.news.collect import gdelt_hint, gdelt_queries, gdelt_urls, hn_urls
    from emithran.news.classify import load_topics

    qs = gdelt_queries()
    topics = load_topics()
    assert len([q for q in qs if q[1]]) == len(topics["cats"]) and len([q for q in qs if q[2]]) == len(topics["countries"])
    for q, cat, _ in qs:
        assert "sourcelang:english" in q and "()" not in q
        assert not q.startswith("(\"") or " OR " in q                          # GDELT rejects parentheses around a single term
    urls = gdelt_urls("3h")
    assert all("timespan=3h" in u and "mode=artlist" in u for u in urls)
    cat, country = gdelt_hint(next(u for u in urls if "die+casting" in u.replace("%20", "+") or "die%20casting" in u))
    assert cat == "die-casting" and country is None
    ind = next(u for u in urls if "sourcecountry%3Aindia" in u)
    assert gdelt_hint(ind) == (None, "IND")
    assert all(urlparse(u).netloc == "hn.algolia.com" and parse_qs(urlparse(u).query)["tags"] == ["story"] for u in hn_urls())


# ------------------------------------------------------------------------------------------ collectors via the control plane
@pytest.fixture()
def client(seeded):
    from emithran.api.main import app

    with TestClient(app) as c:
        yield c


def _job(slug):
    from emithran.db import session_scope
    from emithran.jobs.service import claim_jobs, create_job

    with session_scope() as s:
        jid = create_job(s, slug).id
        claim_jobs(s, 100, "t")
    return jid


def _post(client, jid, url, payload, status=200, ctype="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    p = {"url": url, "status": status, "content_type": ctype, "body_b64": base64.b64encode(body).decode(), "fetched_at": NOW}
    return client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [p]})


def test_gdelt_plan_ingest_filters_tags_and_survives_rate_limit_text(client):
    from emithran.db import session_scope
    from emithran.models import CrawlJob, Document

    jid = _job("gdelt-manufacturing-news")
    plan = client.get(f"/api/fetcher/jobs/{jid}/plan").json()
    from emithran.news.collect import gdelt_queries

    assert len(plan["start_urls"]) == len(gdelt_queries()) and plan["policy"]["download_delay"] >= 10   # one per category and country, polite pace
    url = next(u["url"] for u in plan["start_urls"] if "die" in u["url"] and "casting" in u["url"])
    arts = {"articles": [
        {"url": "https://www.foundry-planet.com/a", "title": "Bühler Megacasting hits the road with the new Volvo EX60", "seendate": "20261020T234500Z", "domain": "foundry-planet.com", "language": "English", "sourcecountry": "United States"},
        {"url": "https://www.foundry-planet.com/b", "title": "FRECH Group sells FRECH ZPF GmbH", "seendate": "20261020T234500Z", "domain": "foundry-planet.com", "language": "English", "sourcecountry": "Germany"},
        {"url": "https://slashgear.com/c", "title": "12 Classic Tech Inventions From The 1960s", "seendate": "20261019T034500Z", "domain": "slashgear.com", "language": "English", "sourcecountry": "United States"}]}
    r = _post(client, jid, url, arts)
    assert r.status_code == 200 and r.json()["stats"]["items_stored"] == 2 and r.json()["stats"]["items_filtered"] == 1
    with session_scope() as s:
        docs = {d.url: d for d in s.execute(select(Document)).scalars()}
    assert "https://slashgear.com/c" not in docs                                           # not manufacturing: never stored
    a, b = docs["https://www.foundry-planet.com/a"], docs["https://www.foundry-planet.com/b"]
    assert "cat:die-casting" in a.categories and "cat:die-casting" in b.categories         # query provenance + trade-domain trust
    assert "pub:USA" in a.categories and "pub:DEU" in b.categories and a.raw["domain"] == "foundry-planet.com"
    assert a.published_at.year == 2026 and a.kind == "news"
    # re-posting is idempotent
    assert _post(client, jid, url, arts).json()["stats"].get("items_stored", 0) == 0
    # GDELT answers rate limits and syntax errors as plain text: a recorded error, never a crash or bad data
    r = _post(client, jid, url, b"Please limit requests to one every 5 seconds or contact kalev.leetaru5@gmail.com", status=429, ctype="text/html")
    assert r.status_code == 200
    with session_scope() as s:
        assert s.get(CrawlJob, jid).stats.get("requests_failed", 0) == 0                    # (the fetcher counts HTTP failures itself)


def test_hackernews_ingest_keeps_manufacturing_only_with_points(client):
    from emithran.db import session_scope
    from emithran.models import Document

    jid = _job("hackernews-manufacturing")
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    hits = {"hits": [
        {"objectID": "1", "title": "Hobby CNC machining and resin casting (2015)", "url": "https://example.org/cnc", "points": 44, "num_comments": 9, "created_at": "2026-10-26T10:00:00Z", "author": "x"},
        {"objectID": "2", "title": "Show HN: my new todo app", "url": "https://example.org/todo", "points": 300, "num_comments": 80, "created_at": "2026-10-26T11:00:00Z", "author": "y"},
        {"objectID": "3", "title": "Ask HN: Injection molding for small batch hardware?", "url": None, "points": 12, "num_comments": 5, "created_at": "2026-10-26T12:00:00Z", "author": "z"}]}
    r = _post(client, jid, "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=cnc&hitsPerPage=20", hits).json()
    assert r["stats"]["items_stored"] == 2 and r["stats"]["items_filtered"] == 1
    with session_scope() as s:
        docs = {d.title: d for d in s.execute(select(Document)).scalars()}
    cnc = docs["Hobby CNC machining and resin casting (2015)"]
    assert cnc.raw["points"] == 44 and cnc.raw["discussion"].endswith("item?id=1") and "cat:machining" in cnc.categories
    ask = docs["Ask HN: Injection molding for small batch hardware?"]
    assert ask.url.endswith("item?id=3") and "cat:plastic-molding" in ask.categories       # Ask HN has no external link: the discussion is the link
    # points move: same story again updates the stored score without counting a new item
    hits["hits"][0]["points"] = 80
    assert _post(client, jid, "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=cnc&hitsPerPage=20", hits).json()["stats"].get("items_stored", 0) == 0
    with session_scope() as s:
        assert s.execute(select(Document).where(Document.title == cnc.title)).scalar_one().raw["points"] == 80


def test_heartbeat_tolerates_null_lists(client):
    jid = _job("hackernews-manufacturing")
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    r = client.post(f"/api/fetcher/jobs/{jid}/heartbeat", json={"stats": None, "logs": None})
    assert r.status_code == 200 and r.json() == {"cancel": False}


# ------------------------------------------------------------------------------------------ feed API
def _docs(n=60):
    from emithran.db import session_scope
    from emithran.models import Document, Source

    with session_scope() as s:
        sid = s.execute(select(Source.id).where(Source.slug == "gdelt-manufacturing-news")).scalar_one()
        base = dt.datetime(2026, 10, 20)
        for i in range(n):
            tags = ["cat:die-casting", "country:IND", "loc:Pune"] if i % 3 == 0 else ["cat:forging", "country:USA"] if i % 3 == 1 else ["cat:pcba", "country:CHN", "loc:Shenzhen"]
            s.add(Document(source_id=sid, kind="news", url=f"https://x.test/{i}", title=f"Headline {i} about {tags[0][4:]}", published_at=base + dt.timedelta(hours=i),
                           categories=tags, raw={"domain": "x.test"}))


def test_feed_filters_paging_since_and_facets(client):
    _docs(60)
    f = client.get("/api/feed", params={"size": 25}).json()
    assert f["total"] == 60 and f["pages"] == 3 and len(f["items"]) == 25 and f["items"][0]["title"].startswith("Headline 59")      # newest first
    p3 = client.get("/api/feed", params={"size": 25, "page": 3}).json()
    assert len(p3["items"]) == 10 and p3["items"][-1]["title"].startswith("Headline 0")                                           # old items stay reachable
    ind = client.get("/api/feed", params={"country": "IND"}).json()
    assert ind["total"] == 20 and all("IND" in i["countries"] and "die-casting" in i["categories"] for i in ind["items"])
    assert client.get("/api/feed", params={"cat": "pcba", "loc": "Shenzhen"}).json()["total"] == 20
    assert client.get("/api/feed", params={"q": "Headline 59"}).json()["total"] == 1
    top = f["max_id"]
    _docs(0)
    from emithran.db import session_scope
    from emithran.models import Document, Source

    with session_scope() as s:
        s.add(Document(source_id=s.execute(select(Source.id).limit(1)).scalar_one(), kind="news", url="https://x.test/new", title="Brand new forging headline",
                       published_at=dt.datetime(2026, 10, 28), categories=["cat:forging", "country:DEU"]))
    new = client.get("/api/feed", params={"since_id": top}).json()
    assert [i["title"] for i in new["items"]] == ["Brand new forging headline"] and new["max_id"] > top           # what the live view prepends
    fx = client.get("/api/feed/facets", params={"days": 3650}).json()
    cats = {c["slug"]: c["count"] for c in fx["categories"]}
    assert cats["die-casting"] == 20 and cats["forging"] == 21 and cats["sand-casting"] == 0                      # every category listed, even empty
    assert {c["iso3"] for c in fx["countries"]} >= {"IND", "CHN", "USA", "DEU", "MEX", "JPN"}                      # every memory/ country listed
    assert fx["locations"][0]["count"] == 20 and {l["name"] for l in fx["locations"]} == {"Pune", "Shenzhen"}


# ------------------------------------------------------------------------------------------ agents: news hotspots and history
def _risk_news():
    from emithran.db import session_scope
    from emithran.models import Document, Source

    with session_scope() as s:
        sid = s.execute(select(Source.id).where(Source.slug == "gdelt-manufacturing-news")).scalar_one()
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        for i in range(6):
            s.add(Document(source_id=sid, kind="news", url=f"https://r.test/{i}", title=f"Strike halts die casting plants in Pune, shortage of aluminum feared ({i})",
                           published_at=now - dt.timedelta(hours=i), categories=["cat:die-casting", "country:IND", "loc:Pune"], raw={"domain": "r.test"}))


def test_news_agent_finds_country_and_category_hotspots_with_corroboration_tags(seeded):
    from emithran.agents.brain import run_brain
    from emithran.db import session_scope
    from emithran.models import AgentFinding

    _risk_news()
    run_brain("manual", use_llm=False)
    with session_scope() as s:
        f = {x.key: x for x in s.execute(select(AgentFinding).where(AgentFinding.agent == "news_risk")).scalars()}
    assert {"news:labour", "news:supply", "news:country:IND", "news:cat:die-casting"} <= set(f)
    hot = f["news:country:IND"]
    assert hot.severity in ("medium", "high") and "country:IND" in hot.tags and hot.entities["locations"] == ["Pune"]
    assert f["news:labour"].entities["categories"] == ["die-casting"] and "country:IND" in f["news:labour"].tags
    assert all(e["url"].startswith("https://r.test/") for e in hot.evidence)                      # evidence links to the real headlines


def test_agent_history_keeps_old_flags_and_pages(client):
    from emithran.agents.brain import run_brain
    from emithran.db import session_scope
    from emithran.models import Document

    _risk_news()
    run_brain("manual", use_llm=False)
    ev1 = client.get("/api/agents/activity", params={"size": 5}).json()
    assert ev1["total"] > 5 and ev1["pages"] > 1 and {e["event"] for e in ev1["items"]} == {"new"}
    with session_scope() as s:                                                                    # the risk headlines disappear from the window
        for d in s.execute(select(Document)).scalars():
            d.published_at = dt.datetime(2020, 1, 1)
    run_brain("manual", use_llm=False)
    resolved = client.get("/api/agents/activity", params={"event": "resolved"}).json()
    assert any(e["title"].startswith("India") or "headline" in e["title"] for e in resolved["items"])
    live = client.get("/api/agents/findings", params={"agent": "news_risk"}).json()["items"]
    old = client.get("/api/agents/findings", params={"agent": "news_risk", "status": "resolved", "limit": 3}).json()
    assert not live and old["total"] >= 4 and len(old["items"]) == 3                               # old flags are kept, paged, not deleted
    page2 = client.get("/api/agents/findings", params={"agent": "news_risk", "status": "resolved", "limit": 3, "offset": 3}).json()
    assert {i["id"] for i in old["items"]}.isdisjoint({i["id"] for i in page2["items"]})
    sev = client.get("/api/agents/findings", params={"status": "all", "min_severity": "high"}).json()["items"]
    assert all(i["severity"] in ("high", "critical") for i in sev)
