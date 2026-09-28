"""Screen capture: a screenshot of the whole screen, attached to Ali's next question.

Taken by Windows' own graphics library (PowerShell + System.Drawing), resized to 1600 px wide (Claude
downsizes bigger images anyway, so the extra pixels would only cost tokens), JPEG, and held in memory
by attach.py like a pasted screenshot. Nothing is written to disk and nothing leaves the PC until he asks.

Triggered by Ctrl+Alt+J anywhere in Windows (main.watch_hotkey) or the 📸 button on the page.
"""
import subprocess
import threading
import time

import attach

WIDTH = 1600
_PS = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Name U -Namespace W -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[W.U]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$full = New-Object Drawing.Bitmap $b.Width, $b.Height
$g = [Drawing.Graphics]::FromImage($full); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $full.Size)
$w = [Math]::Min(%d, $b.Width); $h = [int]($b.Height * $w / $b.Width)
$small = New-Object Drawing.Bitmap $full, $w, $h
$enc = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$p = New-Object Drawing.Imaging.EncoderParameters 1
$p.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), 82L
$ms = New-Object IO.MemoryStream; $small.Save($ms, $enc, $p)
$out = [Console]::OpenStandardOutput(); $out.Write($ms.ToArray(), 0, $ms.Length); $out.Flush()
""" % WIDTH

_latest = {"att": None, "t": 0.0}
_lock = threading.Lock()


def grab():
    """Take the screenshot now; returns the attachment ({id, kind, name, bytes})."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not r.stdout.startswith(b"\xff\xd8"):
        raise RuntimeError(f"Screen capture failed: {r.stderr.decode(errors='ignore')[-200:]}")
    att = attach.add(r.stdout, time.strftime("screen %H-%M-%S.jpg"), "image/jpeg")
    with _lock:
        _latest.update(att=att, t=time.time())
    return att


def latest(max_age=120):
    """The last capture, if it's recent: the page picks it up and puts it in the attachment tray."""
    with _lock:
        return _latest["att"] if _latest["att"] and time.time() - _latest["t"] < max_age else None
