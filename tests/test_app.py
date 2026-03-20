"""Tests for Flask app factory and route registration."""


def test_app_creates(app):
    assert app is not None
    assert app.config["TESTING"] is True


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


def test_search_rejects_missing_query(client):
    resp = client.post("/search", json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_search_rejects_empty_body(client):
    resp = client.post("/search", data="not json", content_type="application/json")
    assert resp.status_code in (400, 500)
