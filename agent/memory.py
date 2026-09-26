"""JARVIS's own memory: one dated markdown file per fact, in memory/ and nowhere else.

Written only when Ali asks, or when he says something that will still matter in
three months — and always said out loud (brain.py enforces that). Never edits or
deletes: to correct a fact, a newer one is written and wins by date.
"""
import re

import clock
import data

DIR = data.ROOT / "memory"
_SLUG = re.compile(r"[^a-z0-9]+")


def remember(fact, topic="general"):
    fact = " ".join(str(fact).split())
    if not fact:
        raise ValueError("empty fact")
    if len(fact) > 500:
        raise ValueError("one fact per memory, under 500 characters")
    DIR.mkdir(exist_ok=True)
    day = clock.uk_today().isoformat()
    slug = _SLUG.sub("-", fact.lower()).strip("-")[:48].rstrip("-") or "fact"
    p = DIR / f"{day}-{slug}.md"
    n = 2
    while p.exists():
        p = DIR / f"{day}-{slug}-{n}.md"
        n += 1
    if DIR.resolve() not in p.resolve().parents:
        raise RuntimeError("refusing to write outside memory/")
    topic = _SLUG.sub("-", str(topic).lower()).strip("-")[:30] or "general"
    with open(p, "x", encoding="utf-8", newline="\n") as f:
        f.write(f"---\ndate: {day}\ntopic: {topic}\n---\n\n{fact}\n")
    return p.name


def all_facts():
    """[{file, date, topic, fact}], oldest first, so newer facts win when read top to bottom."""
    out = []
    if not DIR.exists():
        return out
    for p in sorted(DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"---\s*\n(.*?)\n---\s*\n(.*)", text, re.S)
        meta = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l) if m else {}
        out.append({"file": p.name, "date": meta.get("date", "").strip(), "topic": meta.get("topic", "").strip(),
                    "fact": (m.group(2) if m else text).strip()})
    return out


def prompt_block():
    facts = all_facts()
    if not facts:
        return ""
    lines = [f"- ({f['date']}, {f['topic']}) {f['fact']}" for f in facts]
    return ("\n\n# Things Ali asked you to remember (memory/)\n\nNewer entries override older ones.\n\n"
            + "\n".join(lines))
