"""Tests for the comment threads: REST CRUD, counters and the WebSocket chat."""
from __future__ import annotations

from datetime import timedelta

from app import db as db_module
from app.services import comment_service
from app.utils import new_id, utcnow

from .conftest import ADMIN_HEADERS


def _article(database, **overrides):
    doc = {
        "id": new_id(),
        "slug": f"test-article-{new_id()[:6]}",
        "title": "United edge past Fulham",
        "summary": "A late winner settles it.",
        "status": "Published",
        "views": 0,
        "comments": 0,
        "external": False,
        "tags": [],
        "published_at": utcnow(),
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    doc.update(overrides)
    database[db_module.NEWS].insert_one(dict(doc))
    return doc


def _thread(client, target_id, target_type="story", **params):
    response = client.get(
        "/api/comments", params={"target_type": target_type, "target_id": target_id, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _post(client, target_id, body="Great win, the press was superb.", **extra):
    payload = {"target_type": "story", "target_id": target_id, "body": body, **extra}
    return client.post("/api/comments", json=payload)


# --- REST ------------------------------------------------------------------
def test_thread_of_unknown_article_is_empty(client, database):
    article = _article(database)
    thread = _thread(client, article["id"])
    assert thread["items"] == []
    assert thread["total"] == 0
    assert thread["target_id"] == article["id"]


def test_create_comment_updates_news_counter(client, database):
    article = _article(database)
    response = _post(client, article["id"], author="StretfordEnd", visitor_id="visitor-1")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["comment"]["author"] == "StretfordEnd"
    assert payload["comment"]["initials"] == "ST"
    assert payload["comment"]["likes"] == 0
    assert payload["total"] == 1

    stored = database[db_module.NEWS].find_one({"id": article["id"]})
    assert stored["comments"] == 1

    thread = _thread(client, article["id"])
    assert [item["id"] for item in thread["items"]] == [payload["comment"]["id"]]


def test_default_author_when_blank(client, database):
    article = _article(database)
    comment = _post(client, article["id"], author="   ").json()["comment"]
    assert comment["author"] == comment_service.DEFAULT_AUTHOR
    assert comment["initials"] == "SF"


def test_create_validates_body_and_target(client, database):
    article = _article(database)
    assert _post(client, article["id"], body="   ").status_code == 422
    assert _post(client, article["id"], body="x" * 2500).status_code == 422
    assert client.post(
        "/api/comments",
        json={"target_type": "podcast", "target_id": article["id"], "body": "hi"},
    ).status_code == 422
    assert _post(client, "does-not-exist").status_code == 404


def test_body_is_sanitised(client, database):
    article = _article(database)
    body = "<script>alert('x')</script>Great   win\n\n\n\nloved it"
    comment = _post(client, article["id"], body=body).json()["comment"]
    assert "<script>" not in comment["body"]
    assert "\n\n\n" not in comment["body"]
    assert "Great win" in comment["body"]


def test_replies_are_nested_and_flattened(client, database):
    article = _article(database)
    root = _post(client, article["id"]).json()["comment"]
    reply = _post(client, article["id"], body="Totally agree", parent_id=root["id"]).json()["comment"]
    nested = _post(client, article["id"], body="Same here", parent_id=reply["id"]).json()["comment"]

    thread = _thread(client, article["id"])
    assert thread["total"] == 3
    assert len(thread["items"]) == 1
    replies = thread["items"][0]["replies"]
    assert {item["id"] for item in replies} == {reply["id"], nested["id"]}
    assert all(item["parent_id"] == root["id"] for item in replies)


def test_reply_must_match_thread(client, database):
    first = _article(database)
    second = _article(database, title="Second article")
    root = _post(client, first["id"]).json()["comment"]
    response = _post(client, second["id"], parent_id=root["id"])
    assert response.status_code == 400
    assert _post(client, first["id"], parent_id="ghost").status_code == 404


def test_sorting_modes(client, database):
    article = _article(database)
    base = utcnow() - timedelta(hours=2)
    ids = []
    for index in range(3):
        comment = _post(client, article["id"], body=f"Comment {index}").json()["comment"]
        database[db_module.COMMENTS].update_one(
            {"id": comment["id"]},
            {"$set": {"created_at": base + timedelta(minutes=index * 10), "likes": index}},
        )
        ids.append(comment["id"])

    newest = _thread(client, article["id"], sort="newest")["items"]
    oldest = _thread(client, article["id"], sort="oldest")["items"]
    top = _thread(client, article["id"], sort="top")["items"]
    assert [item["id"] for item in newest] == list(reversed(ids))
    assert [item["id"] for item in oldest] == ids
    assert [item["id"] for item in top] == list(reversed(ids))


def test_like_toggle(client, database):
    article = _article(database)
    comment = _post(client, article["id"]).json()["comment"]

    first = client.post(f"/api/comments/{comment['id']}/like", json={"visitor_id": "v-1"})
    assert first.status_code == 200
    assert first.json()["comment"]["likes"] == 1
    assert first.json()["comment"]["liked"] is True

    again = client.post(f"/api/comments/{comment['id']}/like", json={"visitor_id": "v-1"})
    assert again.json()["comment"]["likes"] == 0
    assert again.json()["comment"]["liked"] is False

    other = client.post(f"/api/comments/{comment['id']}/like", json={"visitor_id": "v-2"})
    assert other.json()["comment"]["likes"] == 1
    assert client.post(f"/api/comments/ghost/like", json={"visitor_id": "v-1"}).status_code == 404


def test_delete_permissions_and_counter(client, database):
    article = _article(database)
    root = _post(client, article["id"], visitor_id="owner").json()["comment"]
    _post(client, article["id"], body="reply", parent_id=root["id"], visitor_id="other")

    forbidden = client.delete(f"/api/comments/{root['id']}", params={"visitor_id": "stranger"})
    assert forbidden.status_code == 403
    wrong_visitor = client.delete(f"/api/comments/{root['id']}", params={"visitor_id": "nope"})
    assert wrong_visitor.status_code == 403

    removed = client.delete(
        f"/api/comments/{root['id']}", params={"admin_key": "test-admin-key"}
    )
    assert removed.status_code == 200
    assert removed.json()["total"] == 0
    assert database[db_module.NEWS].find_one({"id": article["id"]})["comments"] == 0
    assert _thread(client, article["id"])["items"] == []


def test_author_can_delete_own_comment(client, database):
    article = _article(database)
    comment = _post(client, article["id"], visitor_id="owner").json()["comment"]
    response = client.delete(f"/api/comments/{comment['id']}", params={"visitor_id": "owner"})
    assert response.status_code == 200
    assert comment_service.count(database, article["id"]) == 0


def test_count_and_recent_endpoints(client, database):
    article = _article(database)
    for index in range(2):
        _post(client, article["id"], body=f"Message {index}", author=f"Fan {index}")

    counted = client.get(
        "/api/comments/count", params={"target_type": "live", "target_id": article["id"]}
    )
    assert counted.json()["total"] == 2

    recent = client.get(
        "/api/comments/recent",
        params={"target_type": "story", "ids": f"{article['id']},nope", "per_article": 2},
    )
    assert recent.status_code == 200
    items = recent.json()["items"]
    assert len(items[article["id"]]) == 2
    assert items["nope"] == []


def test_story_and_live_share_one_thread(client, database):
    article = _article(database)
    _post(client, article["id"], body="Posted from the story page")
    assert _thread(client, article["id"], target_type="live")["total"] == 1

    response = client.post(
        "/api/comments",
        json={"target_type": "live", "target_id": article["id"], "body": "Posted from live"},
    )
    assert response.status_code == 201
    assert _thread(client, article["id"], target_type="story")["total"] == 2


def test_stats_endpoint(client, database):
    article = _article(database)
    _post(client, article["id"])
    stats = client.get("/api/comments/stats").json()
    assert stats["total"] >= 1
    assert stats["by_target"]["story"]["total"] >= 1


def test_seeded_articles_have_comments(seeded):
    """The startup seed gives recent stories a starter thread."""
    total = seeded[db_module.COMMENTS].count_documents({})
    assert total > 0
    newest = seeded[db_module.NEWS].find_one({"comments": {"$gt": 0}})
    assert newest is not None
    assert comment_service.count(seeded, newest["id"]) == newest["comments"]

    # Idempotent: running the seed again does not duplicate chatter.
    assert comment_service.seed_demo_comments(seeded) == 0


# --- WebSocket ---
def _recv(socket, kind, attempts=6):
    """Read frames until one of ``kind`` arrives (presence frames sit in between)."""
    for _ in range(attempts):
        message = socket.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"Never received a '{kind}' frame")


def _recv_any(socket, kinds, attempts=6):
    for _ in range(attempts):
        message = socket.receive_json()
        if message["type"] in kinds:
            return message
    raise AssertionError(f"Never received any of {kinds}")


def test_socket_welcome_and_broadcast(client, database):
    article = _article(database)
    _post(client, article["id"], body="Already here", author="EarlyBird")

    with client.websocket_connect(f"/api/comments/ws/story/{article['id']}") as socket:
        welcome = socket.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["total"] == 1
        assert welcome["items"][0]["body"] == "Already here"
        assert welcome["online"] == 1

        socket.send_json({"type": "message", "body": "Hello from the socket", "author": "Chatty"})
        broadcast = _recv(socket, "comment")
        assert broadcast["comment"]["author"] == "Chatty"
        assert broadcast["total"] == 2

        socket.send_json({"type": "typing", "typing": True})
        typing = _recv(socket, "typing")
        assert typing["author"] == "Chatty"

        socket.send_json({"type": "ping", "t": 42})
        assert _recv(socket, "pong")["t"] == 42

        socket.send_json({"type": "nonsense"})
        assert _recv(socket, "error")["detail"].startswith("Unknown message type")


def test_socket_message_reaches_other_clients(client, database):
    article = _article(database)
    url = f"/api/comments/ws/live/{article['id']}"
    with client.websocket_connect(url) as first, client.websocket_connect(url) as second:
        assert first.receive_json()["type"] == "welcome"
        assert second.receive_json()["type"] == "welcome"
        first.send_json({"type": "message", "body": "Anyone watching?", "author": "StretfordEnd"})
        seen = _recv(second, "comment")
        assert seen["comment"]["body"] == "Anyone watching?"
        assert seen["comment"]["author"] == "StretfordEnd"
        assert seen["total"] == 1


def test_socket_reports_number_of_connected_fans(client, database):
    article = _article(database)
    url = f"/api/comments/ws/story/{article['id']}"
    with client.websocket_connect(url) as first:
        assert first.receive_json()["type"] == "welcome"
        assert _recv(first, "presence")["online"] == 1
        with client.websocket_connect(url) as second:
            assert second.receive_json()["type"] == "welcome"
            assert _recv(second, "presence")["online"] == 2
            assert _recv(first, "presence")["online"] == 2


def test_socket_rejects_invalid_target(client):
    with client.websocket_connect("/api/comments/ws/podcast/nope") as socket:
        assert socket.receive_json()["type"] == "error"


def test_socket_reports_validation_errors(client, database):
    article = _article(database)
    with client.websocket_connect(f"/api/comments/ws/story/{article['id']}") as socket:
        socket.receive_json()
        socket.send_json({"type": "message", "body": "   "})
        error = _recv(socket, "error")
        assert error["op"] == "create"


def test_socket_like_and_delete(client, database):
    article = _article(database)
    comment = _post(client, article["id"], visitor_id="v-9").json()["comment"]
    with client.websocket_connect(f"/api/comments/ws/story/{article['id']}") as socket:
        socket.receive_json()
        socket.send_json({"type": "like", "comment_id": comment["id"], "visitor_id": "v-9"})
        liked = _recv(socket, "like")
        assert liked["likes"] == 1

        socket.send_json({"type": "delete", "comment_id": comment["id"]})
        deleted = _recv(socket, "deleted")
        assert deleted["total"] == 0


def test_socket_broadcast_reaches_the_live_room(client, database):
    """A comment posted over REST is pushed to sockets on the other surface."""
    article = _article(database)
    with client.websocket_connect(f"/api/comments/ws/live/{article['id']}") as socket:
        socket.receive_json()
        _post(client, article["id"], body="Posted from the story page", author="StoryFan")
        echoed = _recv(socket, "comment")
        assert echoed["comment"]["body"] == "Posted from the story page"
        assert echoed["total"] == 1


def test_admin_delete_via_rest_requires_key(client, database):
    article = _article(database)
    comment = _post(client, article["id"], visitor_id="owner").json()["comment"]
    assert client.delete(f"/api/comments/{comment['id']}").status_code == 403
    assert client.delete(f"/api/comments/{comment['id']}", headers=ADMIN_HEADERS).status_code == 200
    assert client.delete(f"/api/comments/{comment['id']}", headers=ADMIN_HEADERS).status_code == 404


# --- Viewer state (mine / liked flags, no visitor-id leakage) --------------
def test_public_comment_never_exposes_a_visitor_id(client, database):
    article = _article(database)
    created = _post(client, article["id"], visitor_id="secret-visitor").json()["comment"]
    assert "visitor_id" not in created
    thread = _thread(client, article["id"], visitor_id="secret-visitor")
    assert "visitor_id" not in thread["items"][0]
    assert thread["items"][0]["mine"] is True


def test_mine_and_liked_flags_follow_the_requesting_visitor(client, database):
    article = _article(database)
    mine = _post(client, article["id"], body="Mine", visitor_id="visitor-a").json()["comment"]
    theirs = _post(client, article["id"], body="Theirs", visitor_id="visitor-b").json()["comment"]
    client.post(f"/api/comments/{theirs['id']}/like", json={"visitor_id": "visitor-a"})

    as_a = _thread(client, article["id"], visitor_id="visitor-a")["items"]
    flags = {item["id"]: item for item in as_a}
    assert flags[mine["id"]]["mine"] is True
    assert flags[mine["id"]]["liked"] is False
    assert flags[theirs["id"]]["mine"] is False
    assert flags[theirs["id"]]["liked"] is True

    anonymous = _thread(client, article["id"])["items"]
    assert all(item["mine"] is False and item["liked"] is False for item in anonymous)


def test_socket_identity_frame_reports_the_viewer(client, database):
    article = _article(database)
    mine = _post(client, article["id"], body="Earlier", visitor_id="visitor-z").json()["comment"]

    with client.websocket_connect(f"/api/comments/ws/story/{article['id']}") as socket:
        assert socket.receive_json()["type"] == "welcome"
        socket.send_json({"type": "identity", "author": "Zed", "visitor_id": "visitor-z"})
        viewer = _recv(socket, "viewer")
        assert mine["id"] in viewer["mine"]
        assert viewer["liked"] == []


def test_viewer_state_service_lists_authored_and_liked_ids(client, database):
    article = _article(database)
    authored = _post(client, article["id"], visitor_id="visitor-q").json()["comment"]
    other = _post(client, article["id"], visitor_id="visitor-r").json()["comment"]
    client.post(f"/api/comments/{other['id']}/like", json={"visitor_id": "visitor-q"})

    state = comment_service.viewer_state(database, article["id"], "visitor-q")
    assert state["mine"] == [authored["id"]]
    assert state["liked"] == [other["id"]]
    assert comment_service.viewer_state(database, article["id"], None) == {"mine": [], "liked": []}


# --- matchday live chat rooms ----------------------------------------------
def test_matchday_room_accepts_messages_without_an_article(client, database):
    room = "matchday-2026-09-14"
    response = client.post(
        "/api/comments",
        json={
            "target_type": "live",
            "target_id": room,
            "body": "Mainoo has to start this one.",
            "author": "StretfordEnd",
            "visitor_id": "visitor-live",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["comment"]["target_type"] == "live"
    assert database[db_module.NEWS].count_documents({"id": room}) == 0

    thread = _thread(client, room, target_type="live")["items"]
    assert [item["body"] for item in thread] == ["Mainoo has to start this one."]


def test_chat_room_is_recognised_by_shape_only(client, database):
    assert comment_service.is_live_room("matchday-2026-09-14")
    assert not comment_service.is_live_room("matchday-2026-9-14")
    assert not comment_service.is_live_room("story-2026-09-14")
    assert not comment_service.is_live_room("")


def test_live_room_ids_that_are_not_chat_rooms_still_need_an_article(client):
    response = client.post(
        "/api/comments",
        json={"target_type": "live", "target_id": "missing-live-item", "body": "Hello"},
    )
    assert response.status_code == 404


def test_story_targets_cannot_use_a_chat_room_id(client):
    response = client.post(
        "/api/comments",
        json={"target_type": "story", "target_id": "matchday-2026-09-14", "body": "Hello"},
    )
    assert response.status_code == 404


def test_socket_broadcasts_in_a_matchday_room(client, database):
    room = "matchday-2026-09-15"
    with client.websocket_connect(f"/api/comments/ws/live/{room}") as socket:
        assert socket.receive_json()["type"] == "welcome"
        socket.send_json({"type": "comment", "body": "Anyone else nervous?", "author": "Red"})
        created = _recv(socket, "comment")
        assert created["comment"]["body"] == "Anyone else nervous?"
        assert created["comment"]["target_id"] == room
        assert created["total"] == 1


def test_matchday_room_supports_threaded_replies(client, database):
    room = "matchday-2026-09-16"
    parent = client.post(
        "/api/comments",
        json={"target_type": "live", "target_id": room, "body": "Team news is out.", "author": "A"},
    ).json()["comment"]
    reply = client.post(
        "/api/comments",
        json={
            "target_type": "live",
            "target_id": room,
            "body": "No Mainoo again?",
            "author": "B",
            "parent_id": parent["id"],
        },
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["comment"]["parent_id"] == parent["id"]

    thread = _thread(client, room, target_type="live")
    assert thread["total"] == 2
    assert len(thread["items"]) == 1
    assert [item["body"] for item in thread["items"][0]["replies"]] == ["No Mainoo again?"]
