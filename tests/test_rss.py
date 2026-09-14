"""Unit tests for RSS normalisation and news de-duplication."""
from __future__ import annotations

from app.services import news_service
from app.services.rss import parse_bytes

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>BBC Sport - Manchester United</title>
    <item>
      <title>United seal late win at Old Trafford</title>
      <link>https://example.com/united-win</link>
      <description>&lt;p&gt;A &lt;img src="https://cdn.example.com/a.jpg"/&gt; late goal sealed it.&lt;/p&gt;</description>
      <guid>https://example.com/united-win</guid>
      <pubDate>Mon, 01 Sep 2025 12:00:00 GMT</pubDate>
      <media:content url="https://cdn.example.com/hero.jpg" type="image/jpeg" />
    </item>
    <item>
      <title>No link here</title>
    </item>
  </channel>
</rss>
"""


def test_parse_bytes_normalises_entries():
    entries = parse_bytes("https://example.com/feed", SAMPLE_RSS)
    assert len(entries) == 1  # entry without a link is dropped
    entry = entries[0]
    assert entry.title == "United seal late win at Old Trafford"
    assert entry.image == "https://cdn.example.com/hero.jpg"
    assert entry.source == "BBC Sport - Manchester United"
    assert entry.published_at is not None
    assert "<" not in entry.summary


def test_upsert_entry_dedupes(seeded):
    entries = parse_bytes("https://example.com/feed", SAMPLE_RSS)
    assert news_service.upsert_entry(seeded, entries[0]) is True
    # Second insert of the same link/guid is skipped.
    assert news_service.upsert_entry(seeded, entries[0]) is False


def test_list_news_filters_published(seeded):
    items, total = news_service.list_news(seeded, status="Published", category="Transfers")
    assert total >= 1
    assert all(item["category"] == "Transfers" for item in items)


SAMPLE_ARTICLE = """<!doctype html>
<html><head>
<title>United news</title>
<meta charset="utf-8">
<meta name="description" content="Short fallback description.">
<meta property="og:title" content="United seal late win">
<meta content="A late goal &amp; a red card decided it." property="og:description">
<meta property="og:image" content="/images/hero.jpg">
</head><body>...</body></html>
"""


def test_fetch_preview_reads_og_metadata(monkeypatch):
    from app.services import excerpt

    class FakeResponse:
        status_code = 200
        text = SAMPLE_ARTICLE
        url = "https://example.com/united-win"

    monkeypatch.setattr(excerpt.httpx, "get", lambda *a, **k: FakeResponse())
    preview = excerpt.fetch_preview("https://example.com/united-win")

    assert preview["description"] == "A late goal & a red card decided it."
    assert preview["image"] == "https://example.com/images/hero.jpg"


def test_fetch_preview_survives_network_errors(monkeypatch):
    from app.services import excerpt

    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(excerpt.httpx, "get", boom)
    assert excerpt.fetch_preview("https://example.com/x") == {"description": None, "image": None}


def test_enrich_fills_entries_missing_summaries(monkeypatch):
    from app.services import excerpt

    entries = parse_bytes("https://example.com/feed", b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Sky Sports</title>
  <item><title>Transfer latest</title><link>https://example.com/1</link></item>
  <item><title>Team news</title><link>https://example.com/2</link>
    <description>A long enough summary that already explains the story in full detail, mentions the players involved, quotes the manager and gives readers everything they need without any further help at all.</description></item>
</channel></rss>""")
    assert len(entries) == 2

    calls: list[str] = []

    def fake_preview(url, **kwargs):
        calls.append(url)
        return {"description": "Preview blurb for the story.", "image": "https://cdn.example.com/i.jpg"}

    monkeypatch.setattr(excerpt, "fetch_preview", fake_preview)
    assert excerpt.enrich(entries) == 1
    assert calls == ["https://example.com/1"]
    assert entries[0].summary == "Preview blurb for the story."
    assert entries[0].image == "https://cdn.example.com/i.jpg"
    assert entries[1].summary.startswith("A long enough summary")
