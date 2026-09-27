"""What the desktop shortcut runs.

- JARVIS already running on the current code → just open the page.
- Running an older version (the code changed since it started) → stop it, start the new one here.
- Not running → start it here.
"""
import http.client
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402  (also loads .env)

HOST = "127.0.0.1:%d" % main.PORT


def _request(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", main.PORT, timeout=5)
    c.request(method, path, body=body, headers={"Host": HOST, "X-Jarvis": "1", "X-Jarvis-Launcher": "1",
                                                "Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, r.read()


TAB_OPEN_WITHIN = 25                     # an open JARVIS tab checks in every 15s


def running():
    """(code version, seconds since an open tab last checked in) of the running JARVIS, or None."""
    try:
        status, raw = _request("GET", "/api/status")
    except OSError:
        return None                       # nothing listening
    if status != 200:
        return None
    st = json.loads(raw)
    return st.get("code_version", "old"), st.get("page_seen_ago")


def running_version():
    r = running()
    return r[0] if r else None


def main_():
    current = main.code_version()
    now = running()
    if now and now[0] == current:
        if now[1] is not None and now[1] <= TAB_OPEN_WITHIN:
            print("JARVIS is already running and open in your browser. Switch to that tab.")
            time.sleep(3)
        else:
            print("JARVIS is already running the latest version. Opening it.")
            main.open_page(main.ORIGIN)
        return
    if now is not None:
        print("An older JARVIS is running. Restarting it with the latest version...")
        try:
            _request("POST", "/api/shutdown", b"{}")
        except OSError:
            pass
        for _ in range(50):               # wait up to 10s for the port to free up
            if running_version() is None:
                break
            time.sleep(0.2)
        else:
            print("The old JARVIS didn't stop. Close its window (or restart the PC) and try again.")
            input("Press Enter to close.")
            return
    main.main()


if __name__ == "__main__":
    main_()
