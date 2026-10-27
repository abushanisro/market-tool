"""Parsers against real captured payloads (tests/fixtures/replay) and small synthetic inputs."""
import io
import json

from openpyxl import Workbook
from scrapy.http import HtmlResponse, Request, Response, TextResponse

from conftest import FIX
from emithran.crawler.parsers import PARSERS, Follow, ParseContext


def ctx(**opt):
    return ParseContext({"slug": "t", "source_type": opt.pop("_type", "api"), "data_kinds": ["news"]}, opt, job_id=0)


def resp(url, body, cls=TextResponse, ctype="application/json"):
    return cls(url=url, body=body if isinstance(body, bytes) else body.encode(), encoding="utf-8",
               headers={"Content-Type": ctype}, request=Request(url))


def test_frankfurter_latest_and_series():
    r = resp("https://api.frankfurter.dev/v1/latest?base=USD", (FIX / "replay/frankfurter_latest_usd.json").read_text())
    items = list(PARSERS["frankfurter"](r, ctx()))
    inr = next(i for i in items if i["entity"] == "USD/INR")
    assert inr["value"] == 95.12 and inr["period_date"] == "2026-10-27" and len(items) == 29
    r = resp("https://x/series", (FIX / "replay/frankfurter_series_usd.json").read_text())
    assert len(list(PARSERS["frankfurter"](r, ctx()))) == 425


def test_worldbank_skips_missing_values():
    r = resp("https://api.worldbank.org/x", (FIX / "replay/worldbank_NV.IND.MANF.ZS.json").read_text())
    items = list(PARSERS["worldbank_indicator"](r, ctx()))
    assert len(items) == 27  # 32 rows, 5 nulls (USA x4, JPN 2025) not invented
    ind = next(i for i in items if i["country"] == "IND" and i["period_date"] == "2025")
    assert round(ind["value"], 2) == 13.47


def test_rss():
    r = resp("https://www.manufacturingdive.com/feeds/news/", (FIX / "replay/manufacturingdive_news.xml").read_bytes(), ctype="application/rss+xml")
    items = list(PARSERS["rss"](r, ctx()))
    assert len(items) == 5 and all(i["item_type"] == "document" and i["summary"] is None for i in items)


def test_eurostat_jsonstat():
    d = {"label": "IPI", "id": ["geo", "time"], "size": [2, 2], "updated": "2026-09-30",
         "dimension": {"geo": {"category": {"index": {"DE": 0, "FR": 1}}}, "time": {"category": {"index": {"2026-07": 0, "2026-08": 1}}}},
         "value": {"0": 101.2, "1": 99.8, "3": 103.0}}
    items = list(PARSERS["eurostat_jsonstat"](resp("https://e/x", json.dumps(d)), ctx(metric="ipi")))
    assert [(i["country"], i["period_date"], i["value"]) for i in items] == [("DE", "2026-07", 101.2), ("DE", "2026-08", 99.8), ("FR", "2026-08", 103.0)]


def test_comtrade():
    d = {"data": [{"reporterCode": 699, "reporterISO": None, "period": "2024", "cmdCode": "7208", "cmdDesc": "Flat-rolled iron",
                   "flowDesc": "Export", "primaryValue": 1.5e9, "netWgt": 2.0e9}]}
    items = list(PARSERS["comtrade"](resp("https://c/x", json.dumps(d)), ctx()))
    assert {i["metric"] for i in items} == {"Export value", "Export net weight"} and items[0]["country"] == "IND"


def test_pinksheet_landing_and_workbook():
    html = '<a href="/en/doc/abc/related/CMO-Historical-Data-Monthly.xlsx">xlsx</a>'
    out = list(PARSERS["worldbank_pinksheet"](resp("https://www.worldbank.org/en/research/commodity-markets", html, HtmlResponse, "text/html"), ctx()))
    assert isinstance(out[0], Follow) and out[0].url.endswith("CMO-Historical-Data-Monthly.xlsx")
    wb = Workbook(); ws = wb.active; ws.title = "Monthly Prices"   # synthetic layout test (not real prices)
    ws.append(["World Bank Commodity Price Data"]); ws.append([None, "Aluminum", "Copper"]); ws.append([None, "($/mt)", "($/mt)"])
    ws.append(["2026M08", 2500.0, 9000.0]); ws.append(["2026M09", 2550.0, None])
    buf = io.BytesIO(); wb.save(buf)
    r = Response(url="https://x/CMO-Historical-Data-Monthly.xlsx", body=buf.getvalue(), request=Request("https://x"))
    items = list(PARSERS["worldbank_pinksheet"](r, ctx(sheet="Monthly Prices", commodities=["Aluminum", "Copper"])))
    assert len(items) == 3 and items[0]["unit"] == "$/mt" and items[0]["material_name"].startswith("Aluminium")


def test_generic_html_table_and_jsonld():
    html = """<table><tr><th>Grade</th><th>Price</th></tr><tr><td>IS 2062 E250</td><td>58,200</td></tr></table>
    <script type="application/ld+json">{"@type":"Product","name":"PP Homopolymer","offers":{"price":"1.4","priceCurrency":"USD"}}</script>"""
    r = resp("https://s/p", html, HtmlResponse, "text/html")
    rows = list(PARSERS["html_table"](r, ctx(columns={"entity": "Grade", "value": "Price"}, defaults={"unit": "INR/t", "country": "IND"})))
    assert rows[0]["entity"] == "IS 2062 E250" and rows[0]["unit"] == "INR/t"
    j = [i for i in PARSERS["jsonld"](r, ctx()) if isinstance(i, dict)]
    assert j[0]["value"] == "1.4" and "catalog_price" in j[0]["flags"]
