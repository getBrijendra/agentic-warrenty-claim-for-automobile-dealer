"""Headless screenshots for visual checks (Edge/Chrome via the DevTools protocol; waits for live data).

    uv run python scripts/shot.py out.png http://localhost:3000/demo/warranty-claims [wait_s] [width] [height]
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets.sync.client as ws

BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "google-chrome", "chromium"]


def main() -> None:
    out, url = sys.argv[1], sys.argv[2]
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 6
    w, h = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (1800, 1150)
    exe = next(b for b in BROWSERS if os.path.exists(b) or shutil.which(b))
    prof = tempfile.mkdtemp(prefix="wcshot")
    p = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}",
                          "--remote-debugging-port=9333", f"--window-size={w},{h}", "about:blank"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.25)
        with ws.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as c:
            n = 0

            def cmd(method, **params):
                nonlocal n
                n += 1
                c.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    m = json.loads(c.recv())
                    if m.get("id") == n:
                        return m.get("result", {})

            cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
            cmd("Page.navigate", url=url)
            time.sleep(wait)
            img = cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
            with open(out, "wb") as f:
                f.write(base64.b64decode(img["data"]))
            logs = cmd("Runtime.evaluate", expression="document.title")
            print("ok", out, logs.get("result", {}).get("value"))
    finally:
        p.kill()


if __name__ == "__main__":
    main()
