"""JARVIS Mini: a small always-on-top window with today's goals, training, next event and spend.

Talks to the running JARVIS server (http://127.0.0.1:7777) exactly like the page does; it holds no
data and writes no files. Tick a goal by clicking it; type in the box and press Enter to add one.
Drag the header to move it. Right-click for the menu. Run with pythonw so no console window opens.
"""
import http.client
import json
import threading
import tkinter as tk
import webbrowser

PORT = 7777
ORIGIN = f"http://127.0.0.1:{PORT}"
REFRESH_MS = 20000
WIDTH = 280

BG, SURFACE, LINE = "#07060c", "#151022", "#2a2140"
TEXT, MUTED, FAINT = "#ece6ff", "#9a8fb8", "#6b6288"
VIOLET, PINK, GREEN = "#a78bfa", "#ec4899", "#86efac"
MONO = ("Cascadia Mono", 9)
SANS = ("Segoe UI", 10)


def request(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    headers = {"Host": f"127.0.0.1:{PORT}"}
    if body is not None:
        headers.update({"Content-Type": "application/json", "X-Jarvis": "1"})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    if r.status >= 400:
        raise RuntimeError(data.get("error") or f"HTTP {r.status}")
    return data


class Mini:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("JARVIS Mini")
        self.root.overrideredirect(True)                # no Windows title bar: our own header instead
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg=LINE)                     # 1px border colour
        x = self.root.winfo_screenwidth() - WIDTH - 24
        self.root.geometry(f"+{x}+60")
        self.data = None
        self.busy = False

        self.frame = tk.Frame(self.root, bg=BG, padx=12, pady=10)
        self.frame.pack(padx=1, pady=1, fill="both")
        head = tk.Frame(self.frame, bg=BG)
        head.pack(fill="x")
        title = tk.Label(head, text="J.A.R.V.I.S", fg=VIOLET, bg=BG, font=("Cascadia Mono", 10, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="×", fg=FAINT, bg=BG, font=("Segoe UI", 12), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.root.destroy())
        opener = tk.Label(head, text="open", fg=FAINT, bg=BG, font=MONO, cursor="hand2")
        opener.pack(side="right", padx=8)
        opener.bind("<Button-1>", lambda e: webbrowser.open(ORIGIN))
        for w in (head, title):
            w.bind("<ButtonPress-1>", self._start_move)
            w.bind("<B1-Motion>", self._move)

        self.body = tk.Frame(self.frame, bg=BG)
        self.body.pack(fill="both", pady=(8, 0))
        self.entry = tk.Entry(self.frame, bg=SURFACE, fg=TEXT, insertbackground=TEXT, relief="flat",
                              font=SANS, highlightthickness=1, highlightbackground=LINE, highlightcolor=VIOLET)
        self.entry.pack(fill="x", pady=(8, 0), ipady=3)
        self._placeholder(True)
        self.entry.bind("<FocusIn>", lambda e: self._placeholder(False))
        self.entry.bind("<FocusOut>", lambda e: self._placeholder(not self.entry.get()))
        self.entry.bind("<Return>", self._add)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Refresh", command=self.refresh)
        menu.add_command(label="Open JARVIS", command=lambda: webbrowser.open(ORIGIN))
        menu.add_separator()
        menu.add_command(label="Close", command=self.root.destroy)
        self.root.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        self.refresh()

    # ---------------------------------------------------------------- window
    def _start_move(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _placeholder(self, on):
        if on and not self.entry.get():
            self.entry.insert(0, "+ add a goal")
            self.entry.configure(fg=FAINT)
        elif not on and self.entry.cget("fg") == FAINT:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT)

    # ---------------------------------------------------------------- data
    def _background(self, fn):
        """Network calls off the UI thread, so a slow server never freezes the window."""
        def run():
            try:
                data, err = fn(), None
            except Exception as e:
                data, err = None, e
            self.root.after(0, lambda: self._got(data, err))
        threading.Thread(target=run, daemon=True).start()

    def _got(self, data, err):
        self.busy = False
        if err is None:
            self.data = data
        self.render(err)

    def refresh(self):
        if not self.busy:
            self.busy = True
            self._background(lambda: request("GET", "/api/widgets"))
        self.root.after(REFRESH_MS, self.refresh)

    def _tick(self, i, done):
        self._background(lambda: request("POST", "/api/goals", {"action": "tick", "index": i, "done": not done}))

    def _add(self, _e=None):
        text = self.entry.get().strip()
        if not text or self.entry.cget("fg") == FAINT:
            return
        self.entry.delete(0, "end")
        self._background(lambda: request("POST", "/api/goals", {"action": "add", "text": text}))

    # ---------------------------------------------------------------- drawing
    def _section(self, name, badge=""):
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(8, 2))
        tk.Label(row, text=name.upper(), fg=FAINT, bg=BG, font=("Cascadia Mono", 8)).pack(side="left")
        if badge:
            tk.Label(row, text=badge, fg=VIOLET, bg=BG, font=("Cascadia Mono", 8)).pack(side="right")

    def _line(self, text, fg=MUTED, font=SANS):
        tk.Label(self.body, text=text, fg=fg, bg=BG, font=font, anchor="w", justify="left",
                 wraplength=WIDTH - 30).pack(fill="x")

    def render(self, err=None):
        for w in self.body.winfo_children():
            w.destroy()
        d = self.data
        if d is None:
            self._line("JARVIS isn't running." if isinstance(err, (ConnectionError, OSError)) else f"Can't reach JARVIS: {err}",
                       fg=PINK)
            self._line("Start it from the desktop shortcut; this window reconnects by itself.", fg=FAINT)
            return
        if err:
            self._line(f"Couldn't save that: {err}", fg=PINK)

        goals = d.get("goals") or []
        done = sum(g["done"] for g in goals)
        self._section("Today", f"{done}/{len(goals)}" if goals else "")
        if not goals:
            self._line("No goals yet.", fg=FAINT)
        for i, g in enumerate(goals):
            row = tk.Label(self.body, text=("✓  " if g["done"] else "○  ") + g["text"], anchor="w", justify="left",
                           fg=FAINT if g["done"] else TEXT, bg=BG, cursor="hand2", wraplength=WIDTH - 30,
                           font=("Segoe UI", 10, "overstrike") if g["done"] else SANS)
            row.pack(fill="x", pady=1)
            row.bind("<Button-1>", lambda e, i=i, dn=g["done"]: self._tick(i, dn))

        t = d.get("training") or {}
        self._section("Training", f"{t.get('day_streak', 0)}d streak" if t.get("total") else "")
        if t.get("total"):
            last = t.get("last") or {}
            ago = {0: "today", 1: "yesterday"}.get(last.get("days_ago"), f"{last.get('days_ago')} days ago")
            self._line(f"{t['this_week']} this week · {t['week_streak']}-week streak", fg=TEXT)
            self._line(f"Last: {last.get('title', '')} · {ago}")
        else:
            self._line("Nothing logged yet.", fg=FAINT)

        n = d.get("next")
        self._section("Next up")
        if n and not n.get("error"):
            self._line(n["when"], fg=TEXT, font=("Cascadia Mono", 10))
            self._line(n["title"])
        else:
            self._line((n or {}).get("error") or "Nothing in the next two days.", fg=FAINT)

        s = d.get("spend") or {}
        self._section("Spend today", f"of ${s.get('budget', 0):.0f}")
        self._line(f"${s.get('usd', 0):.2f}", fg=GREEN if s.get("usd", 0) < s.get("budget", 1) else PINK,
                   font=("Cascadia Mono", 10))


if __name__ == "__main__":
    Mini().root.mainloop()
