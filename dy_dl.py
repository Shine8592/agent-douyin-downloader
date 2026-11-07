#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Douyin Video Downloader for AI Agents
=====================================

Download public Douyin (抖音) videos through a **real headful Chromium**, not through
an API. This exists because every API path is blocked and yt-dlp's extractor is stale;
the browser route is the one that actually works.

Why a real browser
------------------
Douyin's single-video page renders a MSE player, so the DOM contains `blob:` URLs.
What it also contains is the `<video>` element's `currentSrc` — a plain CDN URL
(`*.zjcdn.com` / `*.douyinvod.com`). Read that, download it with a Referer, done.

The one ordering rule that matters
----------------------------------
Open `https://www.douyin.com/` FIRST in the same browser context, then the video page.
The homepage is what installs the visitor cookies (`ttwid`, `odin_tt`, `__ac_nonce`,
`s_v_web_id`, `__ac_signature`). Without them the video page stays empty.

Requirements
------------
- A headful Chromium/Chrome (headless does not render the player)
- `pip install websocket-client`
- `ffprobe` (optional, for verification)

Usage
-----
    python3 dy_dl.py 'https://v.douyin.com/xxxxx/'
    python3 dy_dl.py 'https://www.douyin.com/video/7705050159461967976'
    python3 dy_dl.py url1 url2 url3 -o ~/Videos/douyin --json
    python3 dy_dl.py --list urls.txt -o ~/Videos/douyin

Exit codes
----------
    0  success (all links)
    1  bad usage
    2  no video source found (deleted / private / not a video page)
    3  every CDN candidate failed
    4  ffprobe rejected the file
    5  partial success (batch mode, at least one link failed)

License: MIT
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

DEFAULT_CDP_PORT = os.environ.get("DY_CDP_PORT", "9222")
DEFAULT_PROFILE = os.environ.get(
    "DY_PROFILE", os.path.join(os.path.expanduser("~"), ".douyin-downloader", "browser"))
DEFAULT_OUTDIR = os.environ.get(
    "DY_OUTDIR", os.path.join(os.path.expanduser("~"), "douyin"))
UA = os.environ.get("DY_UA") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BROWSER_CANDIDATES = [
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "brave-browser", "microsoft-edge",
]

# CDN hosts that serve actual video. Anything else in the DOM is usually the
# PC-client promo clip, which is a real 1080p file and also an advertisement.
GOOD_CDN = ("zjcdn.com", "douyinvod.com", "douyinpic.com", "bytecdn.cn",
            "ixigua.com", "pstatp.com")


def cdp_base(port):
    return "http://127.0.0.1:%s" % port


def _http(path, port, timeout=10):
    return json.load(urllib.request.urlopen(cdp_base(port) + path, timeout=timeout))


def find_browser():
    for name in BROWSER_CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    return None


def pick_display():
    """Pick an X display: env first, then whichever /tmp/.X11-unix socket exists."""
    d = os.environ.get("DISPLAY")
    if d:
        return d
    try:
        socks = sorted(os.listdir("/tmp/.X11-unix"))
    except OSError:
        socks = []
    nums = sorted(int(s[1:]) for s in socks if re.fullmatch(r"X\d+", s))
    return ":%d" % nums[0] if nums else None


def cdp_alive(port):
    try:
        return _http("/json/version", port, timeout=4).get("Browser")
    except Exception:
        return None


def ensure_browser(port, profile, display, verbose=True):
    """Return the Browser string of a CDP-enabled Chromium, starting one if needed."""
    b = cdp_alive(port)
    if b:
        return b

    binary = find_browser()
    if not binary:
        raise RuntimeError(
            "No Chromium/Chrome found. Install one of: " + ", ".join(BROWSER_CANDIDATES))
    if not display:
        raise RuntimeError(
            "No X display available. Douyin's player does not render headless.\n"
            "Start one (e.g. `Xvfb :1 -screen 0 1920x1080x24 &`) and set DISPLAY=:1,\n"
            "or run this script inside `xvfb-run -a`.")

    if verbose:
        print("  [browser] launching %s on DISPLAY=%s" % (binary, display), flush=True)
    os.makedirs(profile, exist_ok=True)
    env = dict(os.environ, DISPLAY=display)
    subprocess.Popen(
        [binary, "--no-sandbox", "--disable-dev-shm-usage",
         "--user-data-dir=" + profile,
         "--remote-debugging-port=" + str(port),
         "--remote-debugging-address=127.0.0.1",
         "--window-position=0,0", "--start-maximized",
         "--autoplay-policy=no-user-gesture-required",
         "about:blank"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)

    for _ in range(40):
        time.sleep(1)
        b = cdp_alive(port)
        if b:
            return b
    raise RuntimeError("Chromium did not expose CDP within 40s")


class Page:
    """Minimal CDP page session."""

    def __init__(self, port):
        import websocket
        self._ws_mod = websocket
        self.port = port
        v = _http("/json/version", port)
        self.bws = websocket.create_connection(v["webSocketDebuggerUrl"], timeout=60)
        self._bid = 0
        self.tid = None
        self.ws = None
        self._pid = 0

    def _bsend(self, method, params=None):
        self._bid += 1
        self.bws.send(json.dumps({"id": self._bid, "method": method,
                                  "params": params or {}}))
        while True:
            m = json.loads(self.bws.recv())
            if m.get("id") == self._bid:
                return m

    def open(self, url):
        r = self._bsend("Target.createTarget", {"url": url})
        self.tid = r["result"]["targetId"]
        time.sleep(2)
        for _ in range(20):
            for t in _http("/json/list", self.port):
                if t.get("id") == self.tid and t.get("webSocketDebuggerUrl"):
                    self.ws = self._ws_mod.create_connection(
                        t["webSocketDebuggerUrl"], timeout=60)
                    self._send("Runtime.enable")
                    return
            time.sleep(1)
        raise RuntimeError("could not attach to the new tab")

    def _send(self, method, params=None):
        self._pid += 1
        self.ws.send(json.dumps({"id": self._pid, "method": method,
                                 "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._pid:
                return m

    def js(self, expr):
        r = self._send("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("result", {}).get("value")

    def close(self):
        for closer in (
            lambda: self._bsend("Target.closeTarget", {"targetId": self.tid}),
            lambda: self.ws and self.ws.close(),
            lambda: self.bws.close(),
        ):
            try:
                closer()
            except Exception:
                pass


JS_INFO = r"""
(() => {
  const v = document.querySelector('video');
  // nudge the player: a paused video with no decoded frame never exposes a CDN URL
  if (v) { try { if (v.paused && v.readyState < 2) { const p = v.play(); if (p && p.catch) p.catch(()=>{}); } } catch (e) {} }
  const bad = u => !u || u.startsWith('blob:') || u.startsWith('data:');
  const srcs = Array.from(document.querySelectorAll('video source'))
                   .map(s => s.src).filter(u => !bad(u));
  let cur = v ? (v.currentSrc || v.src || '') : '';
  if (bad(cur)) cur = '';
  if (cur && srcs.indexOf(cur) < 0) srcs.unshift(cur);
  return JSON.stringify({
    url: location.href,
    title: document.title,
    srcs: srcs,
    dims: v ? [v.videoWidth, v.videoHeight] : [0, 0],
    dur: v ? (isFinite(v.duration) ? v.duration : 0) : 0,
    readyState: v ? v.readyState : -1
  });
})()
"""

WARMUP_URL = "https://www.douyin.com/"


def warm_up(page, verbose=True):
    """Load the homepage once so the visitor cookies get installed."""
    if verbose:
        print("  [warmup] loading douyin.com to install visitor cookies", flush=True)
    page.open(WARMUP_URL)
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(2)
        # Presence of the cookie the video page actually needs is the signal.
        try:
            ok = page.js("document.cookie.indexOf('ttwid') >= 0")
        except Exception:
            ok = None
        if ok:
            # ttwid arrives first; give the rest of the visitor cookies a moment
            time.sleep(3)
            return True
    if verbose:
        print("  [warmup] warning: ttwid not seen yet, continuing anyway", flush=True)
    return False


def resolve(page, url, verbose=True):
    """Follow a share link to the canonical video URL."""
    if "v.douyin.com" not in url and "/share/" not in url:
        return url
    page.open(url)
    for _ in range(15):
        time.sleep(1)
        cur = page.js("location.href") or ""
        if re.search(r"douyin\.com/(video|note)/\d+", cur):
            return cur
    return page.js("location.href") or url


def grab(page, url, wait=30, verbose=True):
    """Open the video page and poll until the <video> element exposes a usable source."""
    page.open(url)
    deadline = time.time() + wait
    last = {}
    while time.time() < deadline:
        time.sleep(2)
        raw = page.js(JS_INFO)
        if not raw:
            continue
        try:
            info = json.loads(raw)
        except Exception:
            continue
        last = info
        # A CDN URL alone is not enough: a still-undecoded player reports 0x0.
        dims = info.get("dims") or [0, 0]
        if info.get("srcs") and (dims[0] > 0 or (info.get("readyState") or 0) >= 1):
            return info
    return last


def pick_sources(srcs):
    """Prefer known CDN hosts; keep the rest as fallback."""
    good = [s for s in srcs if any(h in s for h in GOOD_CDN)]
    rest = [s for s in srcs if s not in good]
    return good + rest


def download(srcs, out_path, verbose=True):
    for i, src in enumerate(srcs, 1):
        try:
            req = urllib.request.Request(src, headers={
                "User-Agent": UA,
                "Referer": "https://www.douyin.com/",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=180) as r, open(out_path, "wb") as f:
                shutil.copyfileobj(r, f)
            size = os.path.getsize(out_path)
            if size > 10_000:
                if verbose:
                    print("  [download] source %d/%d ok, %.0f KB"
                          % (i, len(srcs), size / 1024), flush=True)
                return True
            if verbose:
                print("  [download] source %d too small (%d B), next" % (i, size), flush=True)
        except Exception as e:
            if verbose:
                print("  [download] source %d failed: %s" % (i, str(e)[:90]), flush=True)
    return False


def probe(path):
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,codec_name,width,height",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30).stdout
        return json.loads(out)
    except Exception:
        return None


def safe_name(title, fallback):
    t = re.sub(r"\s*-\s*抖音\s*$", "", title or "")
    t = re.sub(r"[\\/:*?\"<>|\n\r\t#，。、！？；：（）【】《》「」]", "", t)
    t = re.sub(r"\s+", " ", t).strip("_ -")
    return (t[:40].strip("_ -") or str(fallback))


def handle_one(page, url, outdir, wait, verbose):
    """Returns (ok, info_dict)"""
    res = {"input": url, "ok": False, "reason": None}
    final = resolve(page, url, verbose=verbose)
    res["page"] = final
    m = re.search(r"(?:video|note)/(\d+)", final)
    aweme_id = m.group(1) if m else "unknown"
    res["aweme_id"] = aweme_id

    info = grab(page, final, wait=wait, verbose=verbose)
    srcs = info.get("srcs") or []
    if not srcs:
        res["reason"] = ("no_video_source: the page never exposed a media URL — "
                         "deleted, private, region-locked, or not a video page")
        return False, res

    srcs = pick_sources(srcs)
    dims = info.get("dims") or [0, 0]
    title = safe_name(info.get("title"), aweme_id)
    res.update({"title": title, "width": dims[0], "height": dims[1],
                "duration": info.get("dur") or 0, "sources": len(srcs)})
    if verbose:
        print("  [page] %s" % title[:50], flush=True)
        print("  [page] %dx%d, %d candidate source(s)" % (dims[0], dims[1], len(srcs)),
              flush=True)

    out_path = os.path.join(outdir, "%s_%dx%d.mp4" % (title, dims[0], dims[1]))
    if not download(srcs, out_path, verbose=verbose):
        res["reason"] = "all_sources_failed"
        return False, res

    p = probe(out_path)
    if p:
        vs = [s for s in p.get("streams", []) if s.get("codec_type") == "video"]
        dur = float(p.get("format", {}).get("duration") or 0)
        if not vs or dur <= 0:
            res["reason"] = "ffprobe_rejected: file is not playable"
            try:
                os.remove(out_path)
            except OSError:
                pass
            return False, res
        res.update({"codec": vs[0].get("codec_name"),
                    "width": vs[0].get("width") or dims[0],
                    "height": vs[0].get("height") or dims[1],
                    "duration": dur})
        if verbose:
            print("  [verify] %s %sx%s %.2fs"
                  % (res["codec"], res["width"], res["height"], dur), flush=True)

    res.update({"ok": True, "path": os.path.abspath(out_path),
                "size": os.path.getsize(out_path)})
    return True, res


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Download public Douyin videos via a real Chromium (no login needed).")
    ap.add_argument("urls", nargs="*", help="Douyin share or video URLs")
    ap.add_argument("-l", "--list", metavar="FILE",
                    help="read one URL per line from FILE")
    ap.add_argument("-o", "--outdir", default=DEFAULT_OUTDIR, help="output directory")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="browser profile directory")
    ap.add_argument("--port", default=DEFAULT_CDP_PORT, help="CDP port (default 9222)")
    ap.add_argument("--display", default=None, help="X display to use (auto-detected)")
    ap.add_argument("--wait", type=int, default=30, help="seconds to wait for the player")
    ap.add_argument("--json", action="store_true", help="print a JSON report on stdout")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    urls = list(args.urls)
    if args.list:
        try:
            with open(args.list, encoding="utf-8") as f:
                urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except OSError as e:
            print("cannot read list: %s" % e, file=sys.stderr)
            return 1
    if not urls:
        ap.print_help()
        return 1

    verbose = not args.quiet and not args.json
    os.makedirs(args.outdir, exist_ok=True)

    try:
        browser = ensure_browser(args.port, args.profile,
                                 args.display or pick_display(), verbose=verbose)
    except RuntimeError as e:
        print("browser error: %s" % e, file=sys.stderr)
        return 1
    if verbose:
        print("  [browser] %s" % browser, flush=True)

    results = []
    page = Page(args.port)
    try:
        warm_up(page, verbose=verbose)
        for u in urls:
            if verbose:
                print("\n[%d/%d] %s" % (len(results) + 1, len(urls), u), flush=True)
            try:
                ok, res = handle_one(page, u, args.outdir, args.wait, verbose)
            except Exception as e:
                ok, res = False, {"input": u, "ok": False, "reason": "exception: %s" % e}
            results.append(res)
            if verbose and ok:
                print("  [saved] %s" % res["path"], flush=True)
    finally:
        page.close()

    ok_count = sum(1 for r in results if r.get("ok"))
    report = {"total": len(results), "ok": ok_count,
              "failed": len(results) - ok_count, "results": results}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    elif not args.quiet:
        print("\n%d/%d succeeded" % (ok_count, len(results)))

    if ok_count == len(results):
        return 0
    return 5 if ok_count else 2


if __name__ == "__main__":
    sys.exit(main())
