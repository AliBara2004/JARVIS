"""Ali's LLM wiki (Karpathy's pattern), living in his vault:

    JARVIS/Sources/   raw sources: an article, PDF or pasted text, stored once and never changed
    JARVIS/Wiki/      pages JARVIS writes and keeps up to date: topics, people, tools, comparisons
    JARVIS/Wiki/index.md   the catalogue, rebuilt from the pages every time one changes
    JARVIS/Wiki/log.md     append-only record of every ingest, page update and health check

Three operations: ingest (a source arrives, pages are written or updated), query (search_brain already
reads the wiki, and the prompt says to prefer it and cite sources), lint (health check: orphans, broken
links, unsourced or stale pages; JARVIS then fixes what it can).

This module reads and fetches. Every write goes through data.py, which keeps it inside JARVIS/.
"""
import datetime as dt
import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import clock
import data
import vault

MAX_FETCH = 3 * 1024 * 1024        # bytes read from a URL
MAX_SOURCE_CHARS = 60000           # stored in the source note
MAX_EXCERPT = 14000                # handed to the model in one go
STALE_DAYS = 60
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")


# ------------------------------------------------------------------ fetching a source
class _Text(HTMLParser):
    """Readable text of a page: skips scripts, styles, nav, headers, footers, asides and forms."""
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "button"}
    BLOCK = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self.in_title, self.meta_title = [], 0, "", False, ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "meta" and a.get("property") in ("og:title",) and a.get("content"):
            self.meta_title = a["content"]
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self.out.append("## ")
        if tag == "li" and not self.skip:
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, d):
        if self.in_title:
            self.title += d
        elif not self.skip:
            self.out.append(d)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", t)
        return "\n".join(line.strip() for line in t.splitlines()).strip()


def _public_url(url):
    """Only http(s) to the public internet: never this PC or the local network."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Only http:// or https:// links.")
    try:
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError("That address is on this PC or the local network; I only fetch public pages.")
    except socket.gaierror:
        raise ValueError(f"Can't find {u.hostname}.")
    return url


def fetch(url):
    """(title, text) of a web page or PDF."""
    _public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JARVIS personal wiki)",
                                               "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _public_url(r.geturl())                       # no redirects into the local network either
        raw = r.read(MAX_FETCH)
        ctype = r.headers.get("Content-Type", "")
        charset = r.headers.get_content_charset() or "utf-8"
    if "pdf" in ctype or raw[:5] == b"%PDF-":
        text = data._pdf_text(raw)
        title = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]) or url
        if not text.strip():
            raise ValueError("That PDF has no text layer (probably a scan).")
        return title.removesuffix(".pdf"), text
    body = raw.decode(charset, errors="replace")
    if "html" not in ctype and "<html" not in body[:2000].lower():
        return url, body
    p = _Text()
    p.feed(body)
    title = html.unescape((p.meta_title or p.title).strip()) or url
    return title, p.text()


# ------------------------------------------------------------------ what's in the wiki
def pages():
    """Wiki pages (not index/log), newest first: [{title, rel, summary, updated, sources, links, id}]."""
    out = []
    for n in vault.get().notes:
        if not n.rel.startswith(f"{data.NOTES_SUBDIR}/{data.WIKI_DIR}/") or n.title.lower() in data.WIKI_RESERVED:
            continue
        if n.type != "wiki":
            continue
        out.append({"title": n.title, "rel": n.rel, "id": n.id, "summary": n.meta.get("summary", ""),
                    "updated": n.meta.get("updated", ""),
                    "sources": [s.strip() for s in n.meta.get("sources", "").split(";") if s.strip()],
                    "links": {l.strip() for l in LINK.findall(n.text)}})
    return sorted(out, key=lambda p: p["updated"], reverse=True)


def sources():
    return [n for n in vault.get().notes if n.type == "source"]


def index_text():
    ps = sorted(pages(), key=lambda p: p["title"].lower())
    now = clock.uk_now().strftime("%Y-%m-%d %H:%M")
    lines = ["---", "type: wiki-index", f"updated: {now}", "source: jarvis", "---", "",
             "# Wiki index", "",
             "JARVIS keeps this wiki: it files what you send it and updates these pages. "
             "Rebuilt automatically; edit the pages, not this list.", "",
             f"{len(ps)} page{'s' if len(ps) != 1 else ''} · {len(sources())} source{'s' if len(sources()) != 1 else ''} · activity in [[log]]", ""]
    for p in ps:
        extra = f" · {len(p['sources'])} source{'s' if len(p['sources']) != 1 else ''}" if p["sources"] else ""
        lines.append(f"- [[{p['title']}]] — {p['summary'] or 'no summary yet'} ({p['updated'][:10]}{extra})")
    return "\n".join(lines) + "\n"


def log(action, detail):
    stamp = clock.uk_now().strftime("%Y-%m-%d %H:%M")
    header = "---\ntype: wiki-log\nsource: jarvis\n---\n\n# Wiki log\n\nEvery ingest, update and health check, oldest first.\n\n"
    return data.append_line(data.WIKI_DIR, "log.md", f"- {stamp} · {action} · {detail}", header)


# ------------------------------------------------------------------ health check
def lint():
    ps = pages()
    titles = {n.title.lower() for n in vault.get().notes} | {n.rel.rsplit("/", 1)[-1][:-3].lower() for n in vault.get().notes}
    wiki_titles = {p["title"].lower() for p in ps}
    inbound = {t: 0 for t in wiki_titles}
    broken = []
    for p in ps:
        for l in p["links"]:
            if l.lower() in inbound and l.lower() != p["title"].lower():
                inbound[l.lower()] += 1
            if l.lower() not in titles and l.lower() not in data.WIKI_RESERVED:
                broken.append({"page": p["title"], "link": l})
    today = clock.uk_today()
    stale = []
    for p in ps:
        try:
            if (today - dt.date.fromisoformat(p["updated"][:10])).days > STALE_DAYS:
                stale.append(p["title"])
        except ValueError:
            pass
    return {"pages": len(ps), "sources": len(sources()),
            "orphans": sorted(p["title"] for p in ps if inbound[p["title"].lower()] == 0),
            "broken_links": broken, "unsourced": sorted(p["title"] for p in ps if not p["sources"]),
            "stale": stale,
            "catalogue": [{"page": p["title"], "summary": p["summary"], "updated": p["updated"]} for p in ps]}
