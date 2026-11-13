#!/usr/bin/env python3
"""Tell Bing, Yandex and other IndexNow partners which WhatWatts pages are new or changed.

Runs after GitHub Pages has finished deploying (see .github/workflows/indexnow.yml).
Standard library only, no secrets: the key is public by design (its file lives at
https://whatwatts.github.io/<key>.txt).

  python tools/indexnow.py --mode changed   # pages changed in the last commit (default)
  python tools/indexnow.py --mode all       # every URL from sitemap.xml (first run / manual)
  python tools/indexnow.py --dry-run        # print what would be sent, send nothing
"""
import argparse, json, re, subprocess, sys, time, urllib.request, urllib.error

HOST = "whatwatts.github.io"
KEY = "9cd21bff6bf84967b6d69b5a3c442811"
KEY_URL = "https://%s/%s.txt" % (HOST, KEY)
ENDPOINT = "https://api.indexnow.org/indexnow"
SITEMAP = "https://%s/sitemap.xml" % HOST
FOLDERS = ("d/", "best/", "vs/")          # generated pages; the home page is handled separately


def path_to_url(path):
    """Repository file path -> public URL, or None if it is not a page."""
    p = path.replace("\\", "/")
    if p == "index.html":
        return "https://%s/" % HOST
    if p.startswith(FOLDERS) and p.endswith("/index.html"):
        return "https://%s/%s" % (HOST, p[:-len("index.html")])
    return None


def changed_urls():
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", "HEAD~1", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    urls = [path_to_url(f.strip()) for f in out if f.strip()]
    return sorted(set(u for u in urls if u))


def sitemap_urls():
    with urllib.request.urlopen(SITEMAP, timeout=30) as r:
        xml = r.read().decode("utf-8")
    return sorted(set(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)))


def submit(urls):
    ok = True
    for i in range(0, len(urls), 10000):          # IndexNow limit per request
        batch = urls[i:i + 10000]
        body = json.dumps({"host": HOST, "key": KEY, "keyLocation": KEY_URL, "urlList": batch}).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print("IndexNow answered %s for %d URLs" % (r.status, len(batch)))
        except urllib.error.HTTPError as e:
            ok = False
            hint = {403: "key file not found or does not match", 422: "URLs do not belong to the host",
                    429: "too many requests, try later"}.get(e.code, "")
            print("IndexNow error %s %s" % (e.code, hint))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["changed", "all"], default="changed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait for the CDN before sending")
    a = ap.parse_args()

    urls = sitemap_urls() if a.mode == "all" else changed_urls()
    if not urls:
        print("No new or changed pages, nothing to send.")
        return 0
    print("%d URL(s):" % len(urls))
    for u in urls[:20]:
        print("  " + u)
    if len(urls) > 20:
        print("  ... and %d more" % (len(urls) - 20))
    if a.dry_run:
        print("Dry run: nothing sent.")
        return 0
    time.sleep(a.wait)
    return 0 if submit(urls) else 1


if __name__ == "__main__":
    sys.exit(main())
