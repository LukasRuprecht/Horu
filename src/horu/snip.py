"""Snip: drag a box anywhere on screen, and Horu reads just that (translated once).

The game's window (or, before Horu knows it, every monitor) is grabbed first and shown dimmed
in a frameless window covering it, so what's selected is exactly what was on screen. The box comes back as fractions of the
picture, which keeps it right whatever the display scaling.
"""

from __future__ import annotations

import base64
import io
import json
import threading

from PIL import Image

Fractions = tuple[float, float, float, float]  # left, top, right, bottom

# Boxes smaller than this (in screen pixels) are a click, not a drag.
_MIN_SIDE = 8

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: #000; cursor: crosshair; user-select: none; }
  #shot { position: fixed; inset: 0; width: 100vw; height: 100vh; filter: brightness(0.45); }
  #box { position: fixed; display: none; outline: 2px solid #f2c14e; box-shadow: 0 0 0 1px #000;
         background-size: 100vw 100vh; }
  #hint { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); padding: 8px 16px;
          border-radius: 999px; background: #15171d; color: #e8e6e1; font: 14px system-ui, sans-serif; }
</style></head><body>
<img id="shot" draggable="false" alt="">
<div id="box"></div>
<div id="hint">Drag a box around the text to read it &middot; Esc to cancel</div>
<script>
  const box = document.getElementById("box"), hint = document.getElementById("hint");
  // The screenshot comes in after the page loads: a page given as text is capped at 2 MB.
  window.showShot = (uri) => {
    document.getElementById("shot").src = uri;
    box.style.backgroundImage = 'url("' + uri + '")';
  };
  let start = null, done = false;
  const finish = (result) => {
    if (done) return;
    done = true;
    window.pywebview.api.finish(result);
  };
  const rect = (e) => {
    const l = Math.min(start.x, e.clientX), t = Math.min(start.y, e.clientY);
    return { l, t, w: Math.abs(e.clientX - start.x), h: Math.abs(e.clientY - start.y) };
  };
  addEventListener("pointerdown", (e) => {
    if (e.button !== 0) { finish(null); return; }
    start = { x: e.clientX, y: e.clientY };
    hint.style.display = "none";
    try { document.body.setPointerCapture(e.pointerId); } catch {} // not a real pointer (a test)
  });
  addEventListener("pointermove", (e) => {
    if (!start) return;
    const r = rect(e);
    Object.assign(box.style, { display: "block", left: r.l + "px", top: r.t + "px", width: r.w + "px", height: r.h + "px",
                               backgroundPosition: -r.l + "px " + -r.t + "px" });
  });
  addEventListener("pointerup", (e) => {
    if (!start) return;
    const r = rect(e);
    start = null;
    if (r.w < __MIN__ || r.h < __MIN__) { box.style.display = "none"; hint.style.display = ""; return; }
    finish([r.l / innerWidth, r.t / innerHeight, (r.l + r.w) / innerWidth, (r.t + r.h) / innerHeight]);
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") finish(null); });
  addEventListener("contextmenu", (e) => { e.preventDefault(); finish(null); });
</script></body></html>"""


class _Bridge:
    def __init__(self, done) -> None:
        self._done = done

    def finish(self, box) -> None:
        self._done(box)


def select(image: Image.Image, area: dict) -> Fractions | None:
    """Show `image` (a grab of the screen box `area`, in physical pixels) covering that box,
    and wait for a box to be dragged on it: the box as fractions of the image, or None if
    cancelled."""
    import webview

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    shot = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    page = _PAGE.replace("__MIN__", str(_MIN_SIDE))

    chosen: list = []
    finished = threading.Event()

    def done(box) -> None:
        chosen.append(box)
        finished.set()

    window = webview.create_window(
        "Horu snip",
        html=page,
        js_api=_Bridge(done),
        width=min(area["width"], 800),  # placed over the screen once shown
        height=min(area["height"], 600),
        frameless=True,
        easy_drag=False,
        on_top=True,
        focus=True,
        background_color="#000000",
    )
    window.events.closed += finished.set
    window.events.shown += lambda: _cover(window, area)
    window.events.loaded += lambda: (_cover(window, area), window.evaluate_js(f"showShot({json.dumps(shot)})"))
    finished.wait(600)
    try:
        window.destroy()
    except Exception:  # already closed
        pass
    box = chosen[0] if chosen else None
    if not box:
        return None
    left, top, right, bottom = (min(max(float(v), 0.0), 1.0) for v in box)
    return (left, top, right, bottom) if right > left and bottom > top else None


def _cover(window, area: dict) -> None:
    """Put the window exactly over `area` (physical pixels: pywebview's own sizes are scaled
    by the display scaling of one monitor), on top, with keyboard focus so Esc works straight
    away (Horu's own window was just hidden, and Windows doesn't hand focus on by itself)."""
    import sys

    if sys.platform != "win32":
        return
    import ctypes

    try:
        hwnd = window.native.Handle.ToInt32()
    except Exception:
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    # Typed: HWND_TOPMOST (-1) must go as a full-width handle, or the call is refused.
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
    HWND_TOPMOST, SWP_SHOWWINDOW = -1, 0x40
    user32.SetWindowPos(hwnd, HWND_TOPMOST, area["left"], area["top"], area["width"], area["height"], SWP_SHOWWINDOW)
    # Square corners: Windows 11 rounds them, which would show the desktop at the edges.
    ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(1)), 4)
    # A window may take the foreground only from the foreground thread: borrow its input
    # queue for the moment it takes it.
    foreground = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    own = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    attached = foreground and foreground != own and user32.AttachThreadInput(own, foreground, True)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(own, foreground, False)


def crop(image: Image.Image, box: Fractions) -> Image.Image:
    width, height = image.size
    left, top, right, bottom = box
    return image.crop((round(left * width), round(top * height), round(right * width), round(bottom * height)))
