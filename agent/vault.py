"""Folders -> searchable graph.

Notes are nodes, [[wikilinks]] are undirected edges. Search is BM25 over
title + body. All file access goes through data.py.

    python agent/vault.py      # index and print counts + top hubs
"""
import math
import re
import sys
from collections import Counter, defaultdict

import data

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
TOKEN = re.compile(r"[a-z0-9£$€%.]+")
STOP = set("the a an and or of to in on for is are was were be it this that with as at by from i my me you your we".split())


def tokens(s):
    out = []
    for t in TOKEN.findall(s.lower()):
        t = t.strip(".")
        if not t or t in STOP:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]                      # crude plural folding: migrations → migration
        out.append(t)
    return out


class Note:
    __slots__ = ("id", "title", "path", "rel", "type", "text", "meta", "links", "tf", "length", "mtime")

    def __init__(self, id, title, path, rel, type, text, meta):
        self.id, self.title, self.path, self.rel = id, title, path, rel
        self.type, self.text, self.meta = type, text, meta
        self.mtime = 0.0
        self.links = set()


class Vault:
    def __init__(self):
        self.notes = []
        self.edges = set()
        self.unresolved = Counter()
        self.df = Counter()
        self.avglen = 1.0
        self.build()

    # ---------- build ----------
    def build(self):
        by_title = {}
        raw_links = []
        for root, path, text, mtime in data.iter_files():
            rel = path.relative_to(root).as_posix()
            meta, body = _frontmatter(text)
            title = meta.get("title") or path.stem
            n = Note(len(self.notes), title, str(path), rel, _type_for(meta, rel), body, meta)
            n.mtime = mtime
            self.notes.append(n)
            by_title.setdefault(title.lower(), n.id)
            by_title.setdefault(path.stem.lower(), n.id)      # Obsidian links by filename, e.g. "2026-09-28 Title"
            for alias in meta.get("aliases", "").split(","):
                if alias.strip():
                    by_title.setdefault(alias.strip().lower(), n.id)
            raw_links.append(WIKILINK.findall(body))

        for n, targets in zip(self.notes, raw_links):
            for t in targets:
                key = t.strip().split("/")[-1].lower()
                j = by_title.get(key)
                if j is None:
                    self.unresolved[t.strip()] += 1
                elif j != n.id:
                    a, b = sorted((n.id, j))
                    self.edges.add((a, b))
        for a, b in self.edges:
            self.notes[a].links.add(b)
            self.notes[b].links.add(a)

        total = 0
        for n in self.notes:
            toks = tokens(n.title) * 3 + tokens(n.text)
            n.tf = Counter(toks)
            n.length = len(toks)
            total += n.length
            self.df.update(n.tf.keys())
        self.avglen = total / max(1, len(self.notes))

    # ---------- query ----------
    def search(self, query, k=5):
        """BM25. Returns [(score, note)] best first."""
        q = tokens(query)
        if not q:
            return []
        N, k1, b = len(self.notes), 1.4, 0.75
        scored = []
        for n in self.notes:
            s = 0.0
            for t in q:
                f = n.tf.get(t)
                if not f:
                    continue
                idf = math.log(1 + (N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * n.length / self.avglen))
            if s > 0:
                scored.append((s, n))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def hubs(self, k=10):
        return sorted(self.notes, key=lambda n: (-len(n.links), n.title))[:k]

    def type_counts(self):
        return Counter(n.type for n in self.notes)

    def graph_json(self):
        return {
            "mode": data.mode(),
            "version": _version,
            "nodes": [{"id": n.id, "title": n.title, "type": n.type, "deg": len(n.links), "rel": n.rel}
                      for n in self.notes],
            "edges": sorted(self.edges),
        }

    def note_json(self, i):
        n = self.notes[i]
        return {"id": n.id, "title": n.title, "type": n.type, "rel": n.rel, "meta": n.meta,
                "text": n.text, "links": [{"id": j, "title": self.notes[j].title, "type": self.notes[j].type}
                                          for j in sorted(n.links, key=lambda j: -len(self.notes[j].links))]}


_V = None
_version = 0                     # bumps on every rebuild so the UI knows to redraw


def get():
    """Shared index, built on first use."""
    global _V
    if _V is None:
        _V = Vault()
    return _V


def reload():
    """Rebuild after JARVIS writes a note or the vault changes on disk."""
    global _V, _version
    _V = Vault()
    _version += 1
    return _V


def version():
    return _version


def _frontmatter(text):
    m = FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("[]\"'")
    return meta, text[m.end():]


def _type_for(meta, rel):
    if meta.get("type"):
        return meta["type"].lower()
    parts = rel.split("/")
    if len(parts) > 1:
        t = parts[-2].lower()
        return t[:-1] if t.endswith("s") and len(t) > 3 else t
    return "note"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    v = Vault()
    print(f"mode: {data.mode()}   folders: {', '.join(str(f) for f in data.folders())}")
    print(f"{len(v.notes)} notes, {len(v.edges)} links, {sum(v.unresolved.values())} unresolved links\n")
    print("by type:")
    for t, c in v.type_counts().most_common():
        print(f"  {t:<12} {c:>4}")
    print("\ntop 10 hubs:")
    for n in v.hubs(10):
        print(f"  {len(n.links):>4}  {n.title}  ({n.type})")
    if v.unresolved:
        print("\nmost-linked missing notes:", ", ".join(t for t, _ in v.unresolved.most_common(5)))
