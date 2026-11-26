"""Tests for the Firebase stream mirror and helpers."""

import base64
import json

from custom_components.smartnora.api import _jwt_claims, apply_stream_event, escape_firebase_path


def test_escape_firebase_path():
    assert escape_firebase_path("scene.abend") == "scene:abend"
    assert escape_firebase_path("a#b$c[d]") == "a:b:c:d:"


def test_put_root_replaces_tree():
    tree = apply_stream_event({"old": 1}, "put", "/", {"x": {"state": {"on": False}}})
    assert tree == {"x": {"state": {"on": False}}}


def test_put_root_null_clears_tree():
    assert apply_stream_event({"old": 1}, "put", "/", None) == {}


def test_put_and_patch_child():
    tree = apply_stream_event({}, "put", "/sw", {"state": {"on": False}, "update": {"by": "client", "timestamp": 1}})
    tree = apply_stream_event(tree, "patch", "/sw", {"state": {"on": True}, "update": {"by": "server", "timestamp": 2}})
    assert tree["sw"]["state"] == {"on": True}
    assert tree["sw"]["update"]["by"] == "server"


def test_put_nested_and_delete():
    tree = apply_stream_event({}, "put", "/scene/pendingScene", {"deactivate": False})
    assert tree == {"scene": {"pendingScene": {"deactivate": False}}}
    tree = apply_stream_event(tree, "put", "/scene/pendingScene", None)
    assert tree == {"scene": {}}


def test_patch_root():
    tree = apply_stream_event({"a": 1}, "patch", "/", {"b": 2, "a": None})
    assert tree == {"b": 2}


def test_jwt_claims():
    payload = base64.urlsafe_b64encode(json.dumps({"user_id": "u1", "email": "a@b.c"}).encode()).decode().rstrip("=")
    assert _jwt_claims(f"h.{payload}.s") == {"user_id": "u1", "email": "a@b.c"}
