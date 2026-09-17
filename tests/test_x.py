"""Tests for the @SimplyUtd X feed behind the Live United panel.

The RSS fixtures are synthetic (fake handles, ids and image names) so nothing is
reproduced from a real post. Every test replaces the HTTP layer, so the suite
never talks to Nitter or fxtwitter.
"""
from __future__ import annotations

import pytest

from app.services import x_service

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <atom:link href="https://nitter.example/SimplyUtd/rss" rel="self" type="application/rss+xml" />
    <title>SimplyUtd / @SimplyUtd</title>
    <link>https://nitter.example/SimplyUtd</link>
    <description>Twitter feed for: @SimplyUtd.</description>
    <image>
      <title>SimplyUtd / @SimplyUtd</title>
      <link>https://nitter.example/SimplyUtd</link>
      <url>https://nitter.example/pic/pbs.twimg.com%2Fprofile_images%2F1%2Fabc_400x400.jpg</url>
      <width>128</width>
      <height>128</height>
    </image>
    <item>
      <title>Matchday squad confirmed &amp; set</title>
      <dc:creator>@SimplyUtd</dc:creator>
      <description><![CDATA[<p>Matchday squad confirmed &amp; set<br>
<br>
Kick-off is at 15:00.</p>
<img src="https://nitter.example/pic/media%2FAAA111.jpg" style="max-width:250px;" />
<img src="https://nitter.example/pic/media%2FBBB222.jpg" style="max-width:250px;" />]]></description>
      <pubDate>Mon, 14 Sep 2026 16:00:00 GMT</pubDate>
      <guid isPermaLink="false">111</guid>
      <link>https://nitter.example/SimplyUtd/status/111#m</link>
    </item>
    <item>
      <title>Pinned: Highlights from the training ground</title>
      <dc:creator>@SimplyUtd</dc:creator>
      <description><![CDATA[<p>Highlights from the training ground</p>
<a href="https://nitter.example/SimplyUtd/status/222#m">
<br>Video<br>
  <img src="https://nitter.example/pic/amplify_video_thumb%2F999%2Fimg%2Fthumb.jpg" style="max-width:250px;" />
</a>

<hr/>
<blockquote>
<b>Fan Page (@utdfanpage)</b>
<p>
<p>A quoted take &amp; more</p>
<img src="https://nitter.example/pic/media%2FCCC333.jpg" style="max-width:250px;" />
</p>
<footer>
&mdash; <cite><a href="https://nitter.example/utdfanpage/status/333#m">https://nitter.example/utdfanpage/status/333#m</a>
</footer>
</blockquote>]]></description>
      <pubDate>Sun, 13 Sep 2026 09:30:00 GMT</pubDate>
      <guid isPermaLink="false">222</guid>
      <link>https://nitter.example/SimplyUtd/status/222#m</link>
    </item>
    <item>
      <title>RT by @SimplyUtd: Wanderers are on the move for a United defender</title>
      <dc:creator>@UtdJournalist</dc:creator>
      <description><![CDATA[<p>Wanderers are on the move for a United defender</p>
<img src="https://nitter.example/pic/media%2FDDD444.jpg" style="max-width:250px;" />]]></description>
      <pubDate>Mon, 14 Sep 2026 17:05:00 GMT</pubDate>
      <guid isPermaLink="false">444</guid>
      <link>https://nitter.example/UtdJournalist/status/444#m</link>
    </item>
  </channel>
</rss>
"""

_PROFILE = """
{"code": 200, "user": {"screen_name": "SimplyUtd", "name": "SimplyUtd",
 "description": "United media.", "followers": 100, "following": 7, "tweets": 42,
 "url": "https://x.com/SimplyUtd", "location": "",
 "avatar_url": "https://pbs.twimg.com/profile_images/1/abc_normal.jpg",
 "banner_url": "https://pbs.twimg.com/profile_banners/1",
 "verification": {"verified": true, "type": "organization"}}}
"""


_MEDIA = """
{"code": 200, "tweet": {"id": "222", "media": {"all": [
 {"type": "video", "url": "https://video.twimg.com/amplify_video/999/vid/1080x1350/hi.mp4",
  "thumbnail_url": "https://pbs.twimg.com/amplify_video_thumb/999/img/thumb.jpg",
  "duration": 11.04, "width": 1080, "height": 1350, "format": "video/mp4",
  "formats": [
   {"url": "https://video.twimg.com/amplify_video/999/pl/list.m3u8", "container": "m3u8"},
   {"url": "https://video.twimg.com/amplify_video/999/vid/480x600/low.mp4", "bitrate": 950000, "container": "mp4"},
   {"url": "https://video.twimg.com/amplify_video/999/vid/1080x1350/hi.mp4", "bitrate": 10368000, "container": "mp4"}
  ]},
 {"type": "photo", "url": "https://pbs.twimg.com/media/AAA111.jpg"},
 {"type": "gif", "url": "https://video.twimg.com/tweet_video/gif.mp4",
  "thumbnail_url": "https://pbs.twimg.com/tweet_video_thumb/gif.jpg",
  "formats": [{"url": "https://video.twimg.com/tweet_video/gif.mp4", "bitrate": 832000, "container": "mp4"}]}
]}}}
"""


@pytest.fixture()
def media(monkeypatch):
    monkeypatch.setattr(x_service, "_get", lambda url, accept: _MEDIA)
    return _MEDIA


@pytest.fixture(autouse=True)
def _clear_cache():
    x_service.reset_cache()
    yield
    x_service.reset_cache()


@pytest.fixture()
def nitter(monkeypatch):
    """Serve the fixture feed over RSS and the fixture card over JSON."""

    def fake_get(url: str, accept: str) -> str:
        return _PROFILE if url.startswith("https://api.fxtwitter.com") else _RSS

    monkeypatch.setattr(x_service, "_get", fake_get)
    return fake_get


@pytest.fixture()
def offline(monkeypatch):
    monkeypatch.setattr(x_service, "_get", lambda url, accept: None)


# --- Parser ----------------------------------------------------------------- #


def test_parse_feed_reads_the_account_and_rewrites_the_avatar():
    feed = x_service.parse_feed(_RSS)
    assert feed.handle == "SimplyUtd"
    assert feed.name == "SimplyUtd"
    assert feed.avatar == "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"


def test_parse_feed_keeps_the_pinned_post_first_then_newest_first():
    feed = x_service.parse_feed(_RSS)
    assert [post["id"] for post in feed.posts] == ["222", "444", "111"]
    assert [post["is_pinned"] for post in feed.posts] == [True, False, False]


def test_parse_feed_flattens_text_and_maps_media_onto_the_twitter_cdn():
    post = x_service.parse_feed(_RSS).posts[2]
    assert post["text"] == "Matchday squad confirmed & set\n\nKick-off is at 15:00."
    assert post["media"] == [
        {"type": "image", "url": "https://pbs.twimg.com/media/AAA111.jpg"},
        {"type": "image", "url": "https://pbs.twimg.com/media/BBB222.jpg"},
    ]
    assert post["url"] == "https://x.com/SimplyUtd/status/111"
    assert post["created_at"] == "2026-09-14T16:00:00+00:00"


def test_parse_feed_marks_a_video_thumbnail_and_reads_the_quoted_post():
    post = x_service.parse_feed(_RSS).posts[0]
    assert post["media"] == [
        {"type": "video", "url": "https://pbs.twimg.com/amplify_video_thumb/999/img/thumb.jpg"}
    ]
    assert post["text"] == "Highlights from the training ground"
    assert post["quoted"] == {
        "author": "utdfanpage",
        "author_name": "Fan Page",
        "url": "https://x.com/utdfanpage/status/333",
        "text": "A quoted take & more",
        "media": [{"type": "image", "url": "https://pbs.twimg.com/media/CCC333.jpg"}],
    }


def test_parse_feed_attributes_a_retweet_to_its_original_author():
    post = x_service.parse_feed(_RSS).posts[1]
    assert post["author"] == "UtdJournalist"
    assert post["retweeted_by"] == "SimplyUtd"
    assert post["is_retweet"] is True
    assert post["text"] == "Wanderers are on the move for a United defender"
    assert post["url"] == "https://x.com/UtdJournalist/status/444"


def test_parse_feed_returns_nothing_for_markup_that_is_not_a_feed():
    assert x_service.parse_feed("<html>not a feed</html>").posts == []


# --- Fetching --------------------------------------------------------------- #


def test_timeline_merges_the_account_card_and_only_gives_owned_posts_an_avatar(nitter):
    payload = x_service.timeline()
    assert payload["handle"] == "SimplyUtd"
    assert payload["name"] == "SimplyUtd"
    assert payload["avatar"] == "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"
    assert payload["profile"]["followers"] == 100
    assert payload["profile"]["verified"] is True
    assert payload["total"] == 3
    assert [post["author_avatar"] is None for post in payload["posts"]] == [False, True, False]
    assert [post["author_name"] for post in payload["posts"]] == ["SimplyUtd", "UtdJournalist", "SimplyUtd"]


def test_timeline_falls_back_to_the_feed_avatar_when_the_card_is_unavailable(nitter, monkeypatch):
    monkeypatch.setattr(
        x_service, "_get", lambda url, accept: None if "fxtwitter" in url else _RSS
    )
    payload = x_service.timeline()
    assert payload["avatar"] == "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"
    assert payload["profile"] == {}
    assert payload["total"] == 3


def test_timeline_is_cached_between_calls(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, accept: str) -> str:
        calls.append(url)
        return _PROFILE if url.startswith("https://api.fxtwitter.com") else _RSS

    monkeypatch.setattr(x_service, "_get", fake_get)
    assert x_service.timeline()["total"] == 3
    assert x_service.timeline()["total"] == 3
    assert len([url for url in calls if url.endswith("/rss")]) == 1


def test_a_failed_read_is_not_cached_and_returns_an_empty_timeline(offline):
    payload = x_service.timeline()
    assert payload["posts"] == []
    assert payload["total"] == 0
    assert payload["handle"] == "SimplyUtd"


def test_timeline_falls_back_to_the_next_instance(monkeypatch):
    tried: list[str] = []

    def fake_get(url: str, accept: str) -> str | None:
        tried.append(url)
        if "nitter.kareem.one" in url:
            return None
        return _RSS if url.endswith("/rss") else _PROFILE

    monkeypatch.setattr(x_service, "_get", fake_get)
    # Pinned rather than read from the config: this test is about the order the
    # pool is walked in, not about which mirrors happen to be configured today.
    monkeypatch.setattr(
        x_service.settings,
        "x_rss_instances",
        ["https://nitter.kareem.one", "https://nitter.thepixora.com"],
    )
    payload = x_service.timeline()
    assert payload["total"] == 3
    assert payload["source"] == "nitter.thepixora.com"
    assert any("kareem" in url for url in tried)


_DISCOVERY_URL = "https://status.example/api/v1/instances"

# A status-board response covering the shapes that must be skipped: a mirror that
# does not serve RSS, one flagged bad, one that has not answered a ping since,
# and one that is only unproven - the last two are the ones worth trying.
_STATUS = """
{"hosts": [
 {"url": "https://nitter.rssless.example", "domain": "nitter.rssless.example",
  "rss": false, "healthy": true, "recent_pings": [100, 120], "is_bad_host": false},
 {"url": "https://nitter.flagged.example", "domain": "nitter.flagged.example",
  "rss": true, "healthy": true, "recent_pings": [100, 120], "is_bad_host": true},
 {"url": "https://nitter.silent.example", "domain": "nitter.silent.example",
  "rss": true, "healthy": true, "recent_pings": [null, null, null], "is_bad_host": false},
 {"url": "https://nitter.unproven.example", "domain": "nitter.unproven.example",
  "rss": true, "healthy": false, "recent_pings": [100, null, null], "is_bad_host": false},
 {"url": "https://nitter.mirror.example", "domain": "nitter.mirror.example",
  "rss": true, "healthy": true, "recent_pings": [100, 120, 90], "is_bad_host": false}
]}
"""


@pytest.fixture()
def dead_pool(monkeypatch):
    """A curated pool that refuses, with a status board that offers a mirror."""

    monkeypatch.setattr(
        x_service.settings, "x_rss_instances", ["https://nitter.dead.example"]
    )
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_enabled", True)
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_url", _DISCOVERY_URL)
    calls: list[str] = []

    def fake_get(url: str, accept: str) -> str | None:
        calls.append(url)
        if url == _DISCOVERY_URL:
            return _STATUS
        if url.startswith("https://nitter.mirror.example"):
            return _RSS
        return None

    monkeypatch.setattr(x_service, "_get", fake_get)
    return calls


# --- Discovery -------------------------------------------------------------- #


def test_discovery_only_offers_instances_that_serve_rss():
    assert x_service._instances_from_status(_STATUS) == [
        "https://nitter.mirror.example",
        "https://nitter.unproven.example",
    ]


def test_discovery_returns_nothing_for_a_response_that_is_not_the_expected_json():
    assert x_service._instances_from_status("<html>nope</html>") == []
    assert x_service._instances_from_status(None) == []


def test_a_dead_pool_is_topped_up_from_the_status_board(dead_pool):
    payload = x_service.timeline()
    assert payload["total"] == 3
    assert payload["source"] == "nitter.mirror.example"
    assert "https://nitter.dead.example/SimplyUtd/rss" in dead_pool
    assert _DISCOVERY_URL in dead_pool


def test_a_working_pool_never_asks_the_status_board(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_url", _DISCOVERY_URL)
    monkeypatch.setattr(
        x_service, "_get", lambda url, accept: calls.append(url) or _RSS
    )
    assert x_service.timeline()["total"] == 3
    assert _DISCOVERY_URL not in calls


def test_discovery_is_cached_across_reads(dead_pool):
    x_service.timeline()
    x_service.timeline(force=True)
    assert dead_pool.count(_DISCOVERY_URL) == 1


def test_a_failed_discovery_is_not_retried_on_the_next_read(monkeypatch):
    monkeypatch.setattr(
        x_service.settings, "x_rss_instances", ["https://nitter.dead.example"]
    )
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_url", _DISCOVERY_URL)
    calls: list[str] = []

    def fake_get(url: str, accept: str) -> str | None:
        calls.append(url)
        return None

    monkeypatch.setattr(x_service, "_get", fake_get)
    assert x_service.timeline()["total"] == 0
    assert x_service.timeline()["total"] == 0
    assert calls.count(_DISCOVERY_URL) == 1


def test_discovery_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(
        x_service.settings, "x_rss_instances", ["https://nitter.dead.example"]
    )
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_enabled", False)
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_url", _DISCOVERY_URL)
    calls: list[str] = []
    monkeypatch.setattr(
        x_service, "_get", lambda url, accept: calls.append(url) or None
    )
    assert x_service.timeline()["total"] == 0
    assert _DISCOVERY_URL not in calls


def test_the_walk_stops_once_it_runs_out_of_mirrors(monkeypatch):
    """A long pool is capped, so a wall of dead mirrors cannot stall a request."""
    tried: list[str] = []
    monkeypatch.setattr(
        x_service.settings,
        "x_rss_instances",
        [f"https://nitter.dead{index}.example" for index in range(20)],
    )
    monkeypatch.setattr(x_service.settings, "x_rss_discovery_enabled", False)
    monkeypatch.setattr(
        x_service, "_get", lambda url, accept: tried.append(url) or None
    )
    assert x_service.timeline()["total"] == 0
    probed = [url for url in tried if url.endswith("/rss")]
    assert len(probed) == x_service._MAX_MIRRORS
    assert not any("dead6" in url for url in probed)


# --- Route ------------------------------------------------------------------ #


def test_posts_route_returns_the_feed(client, nitter):
    payload = client.get("/api/x/posts").json()
    assert payload["handle"] == "SimplyUtd"
    assert payload["total"] == 3
    assert payload["posts"][0]["is_pinned"] is True
    assert payload["updated_at"] is not None


def test_posts_route_honours_the_limit(client, nitter):
    payload = client.get("/api/x/posts", params={"limit": 1}).json()
    assert len(payload["posts"]) == 1
    assert payload["total"] == 3


def test_posts_route_degrades_to_an_empty_feed(client, offline):
    response = client.get("/api/x/posts")
    assert response.status_code == 200
    assert response.json()["posts"] == []


def test_posts_route_is_never_cached_by_the_browser(client, nitter):
    # The panel decides freshness from the body, so a cached copy would pin it.
    assert client.get("/api/x/posts").headers["cache-control"] == "no-store"


# --- Picking up a new post --------------------------------------------------- #


def _texts(payload: dict) -> list[str]:
    return [post["text"] for post in payload["posts"]]


def _rss_with_new_post(post_id: str, title: str) -> str:
    """The fixture feed with one more item, standing in for a post just made."""
    item = (
        f"<item><title>{title}</title><dc:creator>@SimplyUtd</dc:creator>"
        "<pubDate>Tue, 15 Sep 2026 08:00:00 GMT</pubDate>"
        f'<guid isPermaLink="false">{post_id}</guid>'
        f"<link>https://nitter.example/SimplyUtd/status/{post_id}#m</link></item>"
    )
    return _RSS.replace("</channel>", item + "</channel>")


def _swappable_get(feed: dict[str, str]):
    """A ``_get`` replacement whose RSS body can be swapped mid-test."""

    def get(url: str, accept: str) -> str | None:
        return feed["body"] if url.endswith("/rss") else None

    return get


def test_nothing_is_due_for_revalidation_before_the_first_read():
    assert x_service.revalidate_due() is False


def test_revalidate_does_nothing_before_the_first_read(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(x_service, "_get", lambda url, accept: calls.append(url))
    x_service.revalidate()
    assert calls == []


def test_a_fresh_read_is_not_due_for_revalidation(nitter):
    x_service.timeline()
    assert x_service.revalidate_due() is False


def test_a_poll_does_not_touch_the_mirror_while_the_read_is_fresh(client, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        x_service, "_get", lambda url, accept: calls.append(url) or _RSS
    )
    client.get("/api/x/posts")
    client.get("/api/x/posts")
    assert len([url for url in calls if url.endswith("/rss")]) == 1


def test_revalidate_refreshes_a_stale_read(monkeypatch):
    feed = {"body": _RSS}
    monkeypatch.setattr(x_service, "_get", _swappable_get(feed))

    assert "Full time from Old Trafford" not in _texts(x_service.timeline())

    feed["body"] = _rss_with_new_post("555", "Full time from Old Trafford")
    assert x_service.revalidate_due() is False  # still fresh, so left alone

    x_service._cache.revalidate_at = 0.0  # as if the window had passed
    assert x_service.revalidate_due() is True
    x_service.revalidate()

    assert "Full time from Old Trafford" in _texts(x_service.timeline())
    assert x_service.revalidate_due() is False


def test_revalidate_backs_off_when_every_mirror_is_down(monkeypatch):
    monkeypatch.setattr(x_service, "_get", lambda url, accept: _RSS)
    x_service.timeline()

    monkeypatch.setattr(x_service, "_get", lambda url, accept: None)
    x_service._cache.revalidate_at = 0.0

    x_service.revalidate()

    # The refresh failed, so the next attempt waits a full window rather than
    # firing on every poll.
    assert x_service.revalidate_due() is False


def test_posts_route_serves_a_stale_read_then_refreshes_it(client, monkeypatch):
    feed = {"body": _RSS}
    monkeypatch.setattr(x_service, "_get", _swappable_get(feed))
    assert client.get("/api/x/posts").json()["total"] == 3

    feed["body"] = _rss_with_new_post("555", "Full time from Old Trafford")
    x_service._cache.revalidate_at = 0.0

    stale = client.get("/api/x/posts")
    assert "Full time from Old Trafford" not in _texts(stale.json())

    # The out-of-band refresh has landed by the time the next poll arrives, so
    # the reader never waited on a mirror and still sees the new post.
    assert "Full time from Old Trafford" in _texts(client.get("/api/x/posts").json())


# --- Playable media --------------------------------------------------------- #


def test_fetch_media_picks_the_highest_bitrate_progressive_mp4(media):
    payload = x_service.fetch_media("222")
    assert payload["id"] == "222"
    assert [video["url"] for video in payload["videos"]] == [
        "https://video.twimg.com/amplify_video/999/vid/1080x1350/hi.mp4",
        "https://video.twimg.com/tweet_video/gif.mp4",
    ]
    assert payload["videos"][0]["thumbnail"] == "https://pbs.twimg.com/amplify_video_thumb/999/img/thumb.jpg"
    assert payload["videos"][0]["duration"] == 11.04
    assert payload["videos"][0]["width"] == 1080


def test_fetch_media_ignores_a_post_id_that_is_not_numeric():
    assert x_service.fetch_media("../../etc/passwd") == {"id": "../../etc/passwd", "videos": []}
    assert x_service.fetch_media("") == {"id": "", "videos": []}


def test_fetch_media_caches_and_does_not_re_ask(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, accept: str) -> str:
        calls.append(url)
        return _MEDIA

    monkeypatch.setattr(x_service, "_get", fake_get)
    assert x_service.fetch_media("222")["videos"]
    assert x_service.fetch_media("222")["videos"]
    assert len(calls) == 1


def test_fetch_media_returns_nothing_when_the_lookup_fails(offline):
    assert x_service.fetch_media("222") == {"id": "222", "videos": []}


def test_media_route_returns_playable_urls(client, media):
    payload = client.get("/api/x/media/222").json()
    assert payload["videos"][0]["url"].startswith("https://video.twimg.com/")


def test_media_route_degrades_to_no_videos(client, offline):
    response = client.get("/api/x/media/not-an-id")
    assert response.status_code == 200
    assert response.json() == {"id": "not-an-id", "videos": []}


# --- Video relay ------------------------------------------------------------ #
#
# Twitter's CDN answers 403 to any request carrying a Referer and a browser
# always sends one for a <video> on our page, so the bytes are relayed from the
# server. These tests replace the upstream client, so nothing here is fetched.


class _FakeResponse:
    def __init__(self, status_code: int = 200, headers: dict | None = None, body: bytes = b"video-bytes"):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def iter_bytes(self, size: int):
        yield self._body

    def close(self) -> None:
        self.closed = True


class _FakeClient:
    """Minimal stand-in for ``httpx.Client``, capturing what the relay sends."""

    last: "_FakeClient | None" = None

    def __init__(self, response: _FakeResponse | None = None, error: Exception | None = None):
        self.response = response or _FakeResponse(body=b"video-bytes")
        self.error = error
        self.sent_headers: dict | None = None
        self.url: str | None = None
        self.closed = False
        _FakeClient.last = self

    def build_request(self, method: str, url: str, headers: dict | None = None):
        self.url = url
        self.sent_headers = headers
        return {"method": method, "url": url, "headers": headers}

    def send(self, request, stream: bool = False) -> _FakeResponse:
        if self.error:
            raise self.error
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_client(monkeypatch):
    """Install a fake upstream client; returns the class so calls can be read."""

    def install(response: _FakeResponse | None = None, error: Exception | None = None) -> _FakeClient:
        client = _FakeClient(response, error)
        monkeypatch.setattr(x_service.httpx, "Client", lambda **kwargs: client)
        return client

    return install


def test_video_source_returns_the_best_mp4(media):
    assert x_service.video_source("222") == "https://video.twimg.com/amplify_video/999/vid/1080x1350/hi.mp4"


def test_video_source_is_none_without_a_video(offline):
    assert x_service.video_source("222") is None


def test_open_video_stream_sends_no_referer_and_relays_bytes(fake_client):
    client = fake_client(_FakeResponse(headers={"Content-Type": "video/mp4", "Content-Length": "11"}))
    upstream = x_service.open_video_stream("https://video.example/hi.mp4")

    assert client.sent_headers is not None
    assert "Referer" not in client.sent_headers
    assert client.url == "https://video.example/hi.mp4"
    assert upstream is not None
    assert upstream.status_code == 200
    assert b"".join(upstream.chunks) == b"video-bytes"
    assert upstream.headers["content-type"] == "video/mp4"


def test_open_video_stream_drops_headers_the_player_should_not_see(fake_client):
    fake_client(
        _FakeResponse(
            headers={
                "Content-Type": "video/mp4",
                "Access-Control-Allow-Origin": "*",
                "Set-Cookie": "x=1",
                "Connection": "keep-alive",
            }
        )
    )
    upstream = x_service.open_video_stream("https://video.example/hi.mp4")

    assert upstream is not None
    assert upstream.headers["cache-control"] == "public, max-age=86400"
    assert "set-cookie" not in upstream.headers
    assert "connection" not in upstream.headers


def test_open_video_stream_forwards_a_range_request(fake_client):
    client = fake_client(
        _FakeResponse(
            status_code=206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-1/100"},
        )
    )
    upstream = x_service.open_video_stream("https://video.example/hi.mp4", "bytes=0-1")

    assert client.sent_headers is not None
    assert client.sent_headers["Range"] == "bytes=0-1"
    assert upstream is not None
    assert upstream.status_code == 206
    assert upstream.headers["content-range"] == "bytes 0-1/100"


def test_open_video_stream_ignores_a_missing_range_header(fake_client):
    client = fake_client()
    x_service.open_video_stream("https://video.example/hi.mp4")

    assert client.sent_headers is not None
    assert "Range" not in client.sent_headers


def test_open_video_stream_gives_up_on_a_rejected_upload(fake_client):
    fake_client(_FakeResponse(status_code=403))
    assert x_service.open_video_stream("https://video.example/hi.mp4") is None


def test_open_video_stream_gives_up_when_the_cdn_is_unreachable(fake_client):
    fake_client(error=x_service.httpx.ConnectError("boom"))
    assert x_service.open_video_stream("https://video.example/hi.mp4") is None


def test_open_video_stream_releases_the_client_after_relaying(fake_client):
    client = fake_client()
    upstream = x_service.open_video_stream("https://video.example/hi.mp4")

    assert upstream is not None
    assert b"".join(upstream.chunks) == b"video-bytes"
    assert client.closed is True


def test_stream_route_relays_the_video(client, media, fake_client):
    fake_client(_FakeResponse(headers={"Content-Type": "video/mp4", "Content-Length": "11"}))
    response = client.get("/api/x/media/222/stream")

    assert response.status_code == 200
    assert response.content == b"video-bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "public, max-age=86400"


def test_stream_route_answers_a_range_request_with_206(client, media, fake_client):
    fake_client(
        _FakeResponse(
            status_code=206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-10/11"},
        )
    )
    response = client.get("/api/x/media/222/stream", headers={"Range": "bytes=0-10"})

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-10/11"


def test_stream_route_is_404_when_the_post_has_no_video(client, offline):
    response = client.get("/api/x/media/222/stream")
    assert response.status_code == 404


def test_stream_route_is_404_for_a_post_id_that_is_not_numeric(client):
    response = client.get("/api/x/media/not-an-id/stream")
    assert response.status_code == 404


def test_stream_route_reports_a_dead_cdn(client, media, fake_client):
    fake_client(_FakeResponse(status_code=403))
    response = client.get("/api/x/media/222/stream")
    assert response.status_code == 502
