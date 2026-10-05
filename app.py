"""Flask API + static frontend for the UI Design Auditor."""
import ipaddress, json, os, socket, urllib.parse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, request

from auditor import analyze, db

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
MAX_BYTES = 2_000_000
db.init()


def safe_get(url, hops=3):
    """Fetch a URL while blocking private/loopback targets (SSRF guard), re-checked on every redirect."""
    for _ in range(hops + 1):
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("Only http(s) URLs are allowed")
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError("Private or local addresses are blocked")
        r = requests.get(url, timeout=10, stream=True, allow_redirects=False,
                         headers={"User-Agent": "UIDesignAuditor/1.0"})
        if r.is_redirect:
            url = urllib.parse.urljoin(url, r.headers.get("Location", ""))
            continue
        body = r.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise ValueError("Page is larger than 2 MB")
        return body.decode(r.encoding or "utf-8", errors="replace"), url
    raise ValueError("Too many redirects")


def fetch_page(url):
    html, final = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v)[:5]:
        try:
            css, _ = safe_get(urllib.parse.urljoin(final, link.get("href", "")))
            st = soup.new_tag("style")
            st.string = css
            link.replace_with(st)
        except Exception:
            continue
    if soup.head is not None:
        base = soup.new_tag("base", href=final)
        soup.head.insert(0, base)
    return str(soup), final


def bad(msg, code=400):
    return jsonify(error=msg), code


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.post("/api/audit")
def audit():
    body = request.get_json(silent=True) or {}
    html, source = body.get("html"), "paste"
    try:
        if body.get("url"):
            html, source = fetch_page(body["url"].strip())
        if not html or not html.strip():
            return bad("Provide 'html' or 'url'")
        if len(html) > MAX_BYTES:
            return bad("Markup is larger than 2 MB", 413)
        report = analyze(html)
    except ValueError as e:
        return bad(str(e))
    except requests.RequestException as e:
        return bad(f"Could not fetch URL: {e.__class__.__name__}", 502)
    report["id"] = db.save(source, report)
    report["source"] = source
    if body.get("url"):
        report["html"] = html
    return jsonify(report)


@app.get("/api/reports")
def reports():
    return jsonify(db.list_reports())


@app.get("/api/reports/<int:rid>")
def report(rid):
    r = db.get(rid)
    return jsonify(r) if r else bad("Report not found", 404)


@app.get("/api/reports/<int:rid>/export")
def export(rid):
    r = db.get(rid)
    if not r:
        return bad("Report not found", 404)
    return Response(json.dumps(r, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=audit-{rid}.json"})


@app.delete("/api/reports/<int:rid>")
def remove(rid):
    return jsonify(deleted=True) if db.delete(rid) else bad("Report not found", 404)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("DEBUG") == "1")
