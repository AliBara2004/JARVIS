"""Today's goals: checkboxes under "## Goals" in that day's Daily note (Daily/YYYY-MM-DD.md).

Pure text functions only. data.save_goals is the one thing that writes, and it refuses any change
outside the Goals section, so ticking a goal can't touch the rest of Ali's day note.
"""
import difflib
import re

HEADING = "## Goals"
_HEAD = re.compile(r"^##\s+Goals\s*$", re.I)
_END = re.compile(r"^#{1,2}\s")                       # the section runs to the next H1/H2
_ITEM = re.compile(r"^(\s*[-*] \[)([ xX])(\]\s+)(.+?)\s*$")
MAX_LEN = 200


def _lines(text):
    return (text or "").replace("\r\n", "\n").split("\n")


def _span(lines):
    """(heading index, end index) of the Goals section, or None."""
    for i, line in enumerate(lines):
        if _HEAD.match(line):
            j = i + 1
            while j < len(lines) and not _END.match(lines[j]):
                j += 1
            return i, j
    return None


def _items(lines, sp):
    return [(k, m) for k in range(sp[0] + 1, sp[1]) if (m := _ITEM.match(lines[k]))]


def parse(text):
    lines = _lines(text)
    sp = _span(lines)
    if not sp:
        return []
    return [{"text": m.group(4), "done": m.group(2) != " "} for _, m in _items(lines, sp)]


def without_section(text):
    """The note minus its Goals section, whitespace-normalised: what must never change."""
    lines = _lines(text)
    sp = _span(lines)
    if sp:
        lines = lines[:sp[0]] + lines[sp[1]:]
    return re.sub(r"\s+", " ", "\n".join(lines)).strip()


def clean(item):
    item = re.sub(r"^\s*([-*]\s*)?(\[[ xX]?\]\s*)?", "", str(item)).replace("\n", " ").strip()
    return item[:MAX_LEN]


def add(text, items):
    """Append goals (skipping ones already there). Returns (new_text, added)."""
    lines = _lines(text)
    existing = {g["text"].lower() for g in parse(text)}
    added = []
    for it in map(clean, items):
        if it and it.lower() not in existing:
            existing.add(it.lower())
            added.append(it)
    if not added:
        return text, []
    new = [f"- [ ] {it}" for it in added]
    sp = _span(lines)
    if sp:
        at = (_items(lines, sp)[-1][0] + 1) if _items(lines, sp) else sp[0] + 1
        lines[at:at] = new
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", HEADING] + new
    return "\n".join(lines).rstrip("\n") + "\n", added


def set_done(text, index, done=True):
    lines = _lines(text)
    sp = _span(lines)
    items = _items(lines, sp) if sp else []
    if not 0 <= index < len(items):
        raise IndexError("no such goal")
    k, m = items[index]
    lines[k] = f"{m.group(1)}{'x' if done else ' '}{m.group(3)}{m.group(4)}"
    return "\n".join(lines).rstrip("\n") + "\n"


def match(goals, query):
    """Index of the goal Ali meant ("done the gym" -> "Gym: legs"), or None if nothing is close."""
    q = re.sub(r"[^a-z0-9 ]", " ", query.lower())
    q = re.sub(r"\b(i|ve|have|done|did|finished|the|my|a|an|tick|off|cross|out|goal|that|it)\b", " ", q).split()
    if not goals or not q:
        return None
    best, score = None, 0.0
    for i, g in enumerate(goals):
        words = re.sub(r"[^a-z0-9 ]", " ", g["text"].lower()).split()
        overlap = sum(1 for w in q if any(w == x or (len(w) > 3 and x.startswith(w[:4])) for x in words)) / len(q)
        ratio = difflib.SequenceMatcher(None, " ".join(q), " ".join(words)).ratio()
        s = max(overlap, ratio)
        if s > score:
            best, score = i, s
    return best if score >= 0.5 else None
