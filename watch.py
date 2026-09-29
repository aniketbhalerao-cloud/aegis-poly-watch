#!/usr/bin/env python3
"""AEGIS Polymarket watch — public markets only. No wallet. No orders."""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

PORT = int(os.environ.get("PORT", "8801"))
CACHE = {"ok": False, "error": "loading", "markets": [], "ts": ""}
LOCK = threading.Lock()
STOP = threading.Event()

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AEGIS · Polymarket watch</title>
<style>
:root{--bg:#08090b;--surface:#101215;--line:#1c1f26;--steel:#c5ccd6;--muted:#8b919c;--up:#3fa66a;--down:#c45c5c;--amber:#c4a15a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--steel);font:13px/1.45 "IBM Plex Sans","Segoe UI",sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}
.mark b{letter-spacing:.12em}
.mode{font-size:10px;letter-spacing:.16em;padding:4px 8px;border:1px solid var(--amber);color:var(--amber)}
main{padding:16px 18px;max-width:920px}
h2{margin:0 0 10px;font-size:11px;letter-spacing:.14em;color:var(--muted)}
.note{color:var(--muted);margin:0 0 14px}
.err{color:var(--down)}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-size:10px;letter-spacing:.08em;font-weight:500}
.up{color:var(--up)} .down{color:var(--down)}
</style>
</head>
<body>
<header>
  <div class="mark"><b>AEGIS</b> · POLYMARKET WATCH</div>
  <div class="mode">PUBLIC · NO WALLET</div>
</header>
<main>
  <h2>ACTIVE MARKETS</h2>
  <p class="note" id="meta">loading…</p>
  <p class="note">Watch only. Type to filter. No bets.</p>
  <input id="q" placeholder="search markets" style="width:100%;max-width:420px;margin:0 0 14px;padding:8px;background:#101215;border:1px solid #1c1f26;color:#c5ccd6"/>
  <table>
    <thead><tr><th>QUESTION</th><th>YES</th><th>NO</th><th>24H VOL</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
let ALL = [];
function paint(){
  const q = (document.getElementById("q").value||"").toLowerCase();
  const rows = ALL.filter(m => !q || (m.question||"").toLowerCase().includes(q));
  document.getElementById("rows").innerHTML = rows.map(m =>
    `<tr><td>${m.question}</td><td class="up">${m.yes}</td><td class="down">${m.no}</td><td>${m.vol}</td></tr>`
  ).join("");
}
async function load(){
  const s = await (await fetch("/api/markets")).json();
  document.getElementById("meta").textContent = s.ok
    ? ("updated " + s.ts + " · " + s.markets.length + " markets")
    : ("error: " + (s.error||"unknown"));
  document.getElementById("meta").className = "note" + (s.ok?"":" err");
  ALL = s.markets || [];
  paint();
}
document.getElementById("q").addEventListener("input", paint);
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "AEGIS-poly-watch/1.0"})
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_markets():
    q = urllib.parse.urlencode(
        {
            "active": "true",
            "closed": "false",
            "limit": "50",
            "order": "volume24hr",
            "ascending": "false",
        }
    )
    url = f"https://gamma-api.polymarket.com/markets?{q}"
    raw = _get(url)
    if not isinstance(raw, list):
        raise RuntimeError("unexpected gamma response")
    out = []
    for m in raw:
        prices = m.get("outcomePrices") or "[]"
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = []
        yes = prices[0] if len(prices) > 0 else None
        no = prices[1] if len(prices) > 1 else None
        vol = m.get("volume24hr") or 0
        try:
            vol_s = f"{float(vol):,.0f}"
        except (TypeError, ValueError):
            vol_s = "—"
        out.append(
            {
                "question": m.get("question") or m.get("slug") or "—",
                "yes": f"{float(yes):.3f}" if yes is not None else "—",
                "no": f"{float(no):.3f}" if no is not None else "—",
                "vol": vol_s,
            }
        )
    return out


def refresher():
    while not STOP.is_set():
        try:
            markets = fetch_markets()
            with LOCK:
                CACHE.update(
                    {
                        "ok": True,
                        "error": "",
                        "markets": markets,
                        "ts": time.strftime("%H:%M:%S UTC", time.gmtime()),
                    }
                )
            print("watch ok", len(markets), "markets", flush=True)
        except Exception as e:
            with LOCK:
                CACHE.update({"ok": False, "error": str(e)})
            print("watch fail", e, flush=True)
        STOP.wait(20)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if self.path == "/api/markets":
            with LOCK:
                self._send(200, json.dumps(CACHE))
            return
        self._send(404, b"{}")


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "unknown"
    finally:
        s.close()


def main():
    ip = lan_ip()
    print("PHONE WATCH")
    print("Mac:   http://127.0.0.1:%s" % PORT)
    print("Phone: http://%s:%s" % (ip, PORT))
    print("Public watch. Bind 0.0.0.0 port", PORT)
    threading.Thread(target=refresher, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    if os.environ.get("PORT") is None:
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        STOP.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
