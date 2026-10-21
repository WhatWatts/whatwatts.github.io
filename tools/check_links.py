#!/usr/bin/env python3
"""
WhatWatts weekly link check.

Reads the three Google Sheets listed in index.html (const URLS), collects every
buy_am / buy_ali / yt link and checks that it still works.

Result:
  report.md  - human-readable report (what to fix)
  GITHUB_OUTPUT problems=true|false   (when run inside GitHub Actions)

Notes on accuracy:
  * YouTube links are checked through YouTube's oEmbed endpoint, which reliably says
    whether a video was removed or made private.
  * Amazon and AliExpress block many automated requests. A "blocked" answer
    (403, 429, 503, captcha) is NOT reported as broken, only counted.
    Only clear failures are reported: 404/410, DNS errors, dead hosts.
"""
import argparse, concurrent.futures as cf, csv, io, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 25
KINDS = {"buy_am": "Amazon", "buy_ali": "AliExpress", "yt": "YouTube"}


def sheet_urls(index_path):
    src = open(index_path, encoding="utf-8").read()
    block = re.search(r"const URLS\s*=\s*\{(.*?)\};", src, re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block))


def fetch(url, method="GET", read=0):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(read) if read else b""
        return r.status, r.geturl(), body


def load_rows(url):
    status, _, _ = (0, "", b"")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def youtube_id(url):
    u = urllib.parse.urlparse(url)
    if u.netloc.endswith("youtu.be"):
        return u.path.strip("/").split("/")[0] or None
    if "youtube.com" in u.netloc:
        q = urllib.parse.parse_qs(u.query)
        if "v" in q:
            return q["v"][0]
        m = re.match(r"^/(shorts|embed|live)/([\w-]{6,})", u.path)
        if m:
            return m.group(2)
    return None


def check_youtube(url):
    if not youtube_id(url):
        return check_generic(url, "")
    api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    try:
        fetch(api)
        return "ok", ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return "dead", "video removed, private or not embeddable (HTTP %d)" % e.code
        return "unknown", "HTTP %d" % e.code
    except Exception as e:
        return "unknown", type(e).__name__


def check_generic(url, kind):
    last = ""
    for attempt in range(2):
        try:
            status, final, body = fetch(url, read=60000)
            low = body.decode("utf-8", "ignore").lower()
            if "captcha" in low or "robot check" in low or "automated access" in low:
                return "blocked", "bot check"
            return "ok", ""
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return "dead", "HTTP %d (page not found)" % e.code
            if e.code in (401, 403, 405, 429, 451, 503, 999):
                return "blocked", "HTTP %d" % e.code
            last = "HTTP %d" % e.code
            if e.code < 500:
                return "dead", last
        except urllib.error.URLError as e:
            last = str(getattr(e, "reason", e))
        except Exception as e:
            last = type(e).__name__
        time.sleep(2)
    return "dead", "unreachable (%s)" % last


def check(job):
    sheet, device, kind, url = job
    if not re.match(r"^https?://", url):
        return job, "dead", "not a valid web address"
    state, why = check_youtube(url) if kind == "yt" else check_generic(url, kind)
    return job, state, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="index.html")
    ap.add_argument("--report", default="report.md")
    args = ap.parse_args()

    jobs = []
    for sheet, url in sheet_urls(args.index).items():
        try:
            rows = load_rows(url)
        except Exception as e:
            print("Could not read sheet %s: %s" % (sheet, e), file=sys.stderr)
            jobs.append((sheet, "(sheet)", "sheet", url))
            continue
        for r in rows:
            for col in KINDS:
                v = (r.get(col) or "").strip()
                if v and v != "#":
                    jobs.append((sheet, r.get("name", "?"), col, v))
    print("Checking %d links..." % len(jobs))

    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(check, jobs):
            results.append(res)

    dead = [r for r in results if r[1] == "dead"]
    blocked = [r for r in results if r[1] == "blocked"]
    unknown = [r for r in results if r[1] == "unknown"]
    ok = [r for r in results if r[1] == "ok"]

    lines = ["# Link check report", "",
             "Checked **%d** links: **%d** fine, **%d** broken, %d could not be verified (blocked by the shop or YouTube)."
             % (len(results), len(ok), len(dead), len(blocked) + len(unknown)), ""]
    if dead:
        lines += ["## Broken links (please fix)", "", "| Device | Sheet | Link type | Problem | Link |", "|---|---|---|---|---|"]
        for (sheet, device, col, url), _, why in dead:
            lines.append("| %s | %s | %s | %s | %s |" % (device.replace("|", "/"), sheet, KINDS.get(col, col), why, url))
        lines.append("")
    else:
        lines += ["No broken links found.", ""]
    if blocked or unknown:
        lines += ["<details><summary>Could not verify (%d)</summary>" % (len(blocked) + len(unknown)), ""]
        for (sheet, device, col, url), st, why in blocked + unknown:
            lines.append("- %s (%s, %s): %s - %s" % (device, sheet, KINDS.get(col, col), why, url))
        lines += ["", "</details>"]
    open(args.report, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:4]))

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("problems=%s\n" % ("true" if dead else "false"))


if __name__ == "__main__":
    main()
