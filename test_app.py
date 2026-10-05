import os, tempfile
os.environ["AUDITOR_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
import pytest
from auditor import analyze
from app import app, safe_get

BAD = """<html><head><style>.hero{background:#f4a261}.hero p{color:#fff;font-size:11px}</style></head>
<body><div class="hero"><p>Small-batch roasts</p></div><img src="x.png"><button></button><input type="email" placeholder="e"><a href="#">click here</a></body></html>"""
GOOD = """<!DOCTYPE html><html lang="en"><head><title>Ok</title><meta name="viewport" content="width=device-width">
<style>body{color:#111;background:#fff;font-size:16px}</style></head><body><h1>Hello</h1><p>Readable text.</p>
<img src="a.png" alt="A" width="10" height="10"><label for="e">Email</label><input id="e" type="email"></body></html>"""

def titles(r): return {i["title"] for i in r["issues"]}

def test_detects_core_problems():
    t = titles(analyze(BAD))
    assert {"Low colour contrast", "Text too small", "Image missing alt text", "Control has no accessible name",
            "Form field has no label", "Vague link text", "Missing language attribute"} <= t

def test_clean_page_scores_high():
    r = analyze(GOOD)
    assert r["score"]["total"] >= 95 and not r["issues"]

def test_specificity_and_important():
    r = analyze('<html lang=en><head><title>t</title><style>p{color:#777}#a{color:#000}p.b{color:#fff!important}</style></head><body><h1>x</h1><p id=a>ok</p><p class=b>bad</p></body></html>')
    assert [i["target"] for i in r["issues"] if i["title"] == "Low colour contrast"] == ["p.b"]

def test_ssrf_blocked():
    with pytest.raises(ValueError):
        safe_get("http://127.0.0.1:5000/")

def test_api_roundtrip():
    c = app.test_client()
    j = c.post("/api/audit", json={"html": BAD}).get_json()
    assert j["id"] and j["score"]["total"] < 100
    assert c.get("/api/reports").get_json()[0]["id"] == j["id"]
    assert c.get(f"/api/reports/{j['id']}/export").status_code == 200
    assert c.delete(f"/api/reports/{j['id']}").status_code == 200
    assert c.get(f"/api/reports/{j['id']}").status_code == 404
    assert c.post("/api/audit", json={}).status_code == 400
    assert c.post("/api/audit", json={"url": "http://localhost/"}).status_code == 400
    assert c.get("/").status_code == 200
