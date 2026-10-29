#!/usr/bin/env python3
"""
WhatWatts: builds one static, search-engine-friendly page per device.

Reads the three published sheets (addresses from index.html, const URLS), and writes
    d/<cables|chargers|powerbanks>/<slug>/index.html   one page per device
    sitemap.xml                                         home page + all device pages
It never touches index.html, media/ or anything else. The main site works exactly as before:
every page has a button "Open in the full database" that leads to the same device there.

Safety: if a sheet cannot be downloaded or looks wrong, the old pages of that sheet are kept.
Usage:  python tools/build_pages.py [index.html] [out_dir=.]
"""
import datetime, html, os, re, shutil, sys
from urllib.parse import quote

import csv, io, time, urllib.request

UA = "Mozilla/5.0 (compatible; WhatWattsPages/1.0)"


def sheet_urls(index_path):
    """Published CSV addresses from `const URLS = {...}` in index.html."""
    src = open(index_path, encoding="utf-8").read()
    block = re.search(r"const URLS\s*=\s*\{(.*?)\};", src, re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block))


def download(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8-sig")
        except Exception as e:                      # network hiccup: wait and retry
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError("download failed: %s" % last)


def parse(text):
    """Return (header, rows); refuse anything that does not look like the real table."""
    if re.match(r"\s*<(!doctype|html)", text, re.I):
        raise ValueError("got an HTML page instead of CSV (sheet unpublished or link changed)")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty answer")
    header = [h.strip() for h in rows[0]]
    if "name" not in header:
        raise ValueError("no 'name' column in the first row")
    data = [r for r in rows[1:] if any(c.strip() for c in r)]
    if not data:
        raise ValueError("table has a header but no rows")
    return header, data

SITE = "https://whatwatts.github.io"
TYPE_LABEL = {"cables": "cable", "chargers": "charger", "powerbanks": "power bank"}
TYPE_PLURAL = {"cables": "Cables", "chargers": "Chargers", "powerbanks": "Power banks"}
BEACON = "00320e9ea32942e3a3115cb698965612"           # Cloudflare Web Analytics token (same as index.html)
E = html.escape


# ---------------------------------------------------------------- helpers
def slug(name):                                         # identical to slug() in index.html
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()))


def num(v):
    try:
        x = float(str(v).strip().replace(",", "."))
        return x if x == x else None
    except Exception:
        return None


def fmt(x):
    if x is None:
        return ""
    return str(int(x)) if x == int(x) else ("%g" % round(x, 2))


def truthy(v):
    return str(v or "").strip().lower() in ("true", "yes", "1", "да")


def has(v):
    return str(v or "").strip() not in ("", "–", "-", "—")


def dropped(v):
    x = num(v)
    return "Throttled: power dropped to %s W" % fmt(x) if x is not None else "Throttled"


def temp(c):
    x = num(c)
    return "" if x is None else "%s °C (%d °F)" % (fmt(x), round(x * 9 / 5 + 32))


def curve_max(text, unit):
    vals = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*" + unit, str(text or ""))]
    return max(vals) if vals else None


def first_num(raw):
    m = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
    return float(m.group()) if m else None


def photo_urls(row, cat, typ):
    out = []
    for item in str(row.get("photos_" + cat) or "").split(","):
        f = item.split("|")[0].strip()
        if f:
            out.append("%s/media/%s/%s/%s/%s" % (SITE, typ, slug(row["name"]), cat, quote(f)))
    return out


# ---------------------------------------------------------------- facts per device type
def facts_cables(r):
    f = []
    if has(r.get("conn")): f.append(("Connector", r["conn"]))
    if num(r.get("len")): f.append(("Length", "%s m" % fmt(num(r["len"]))))
    if num(r.get("pwr")): f.append(("Max power", "%s W" % fmt(num(r["pwr"]))))
    if num(r.get("res")): f.append(("Resistance", "%s mΩ" % fmt(num(r["res"]))))
    if has(r.get("spd_lbl")):
        s = r["spd_lbl"]
        if num(r.get("adv_spd")) and num(r.get("spd")) and num(r["spd"]) > num(r["adv_spd"]):
            s += " (advertised %s Gbps)" % fmt(num(r["adv_spd"]))
        f.append(("Data speed", s))
    f.append(("E-marker", "Yes" if truthy(r.get("emark")) else "No"))
    f.append(("Built-in display", "Yes" if truthy(r.get("display")) else "No"))
    p = num(r.get("pwr")) or 0
    tier = "140 W test (28 V / 5 A)" if p >= 240 else ("100 W test (20 V / 5 A)" if p >= 100 else "60 W test (20 V / 3 A)")
    if has(r.get("stress_pass")):
        s = "Passed" if truthy(r.get("stress_pass")) else "Throttled"
        t = [x for x in ("Connector " + temp(r.get("stress_conn_c")) if temp(r.get("stress_conn_c")) else "",
                         "cable " + temp(r.get("stress_cable_c")) if temp(r.get("stress_cable_c")) else "") if x]
        f.append(("30-minute load test", "%s, %s%s" % (tier, s, (" · " + ", ".join(t)) if t else "")))
    return f


def facts_chargers(r):
    f = []
    if has(r.get("ports")): f.append(("Ports", r["ports"]))
    if num(r.get("pwr")): f.append(("Power per port", "%s W" % fmt(num(r["pwr"]))))
    if num(r.get("total_pwr")): f.append(("Total power", "%s W" % fmt(num(r["total_pwr"]))))
    eff = curve_max(r.get("eff_curve"), "%") or num(r.get("eff"))
    if eff: f.append(("Peak efficiency", "%s%%" % fmt(round(eff, 1))))
    rip = curve_max(r.get("ripple_curve"), "mV") or num(r.get("ripple"))
    if rip: f.append(("Peak ripple", "%s mV" % fmt(rip)))
    proto = ", ".join(x for x in [r.get("pd", "")] + [s.strip() for s in str(r.get("protocols_extra") or "").split(",")]
                      + (["Xiaomi Turbo Charge"] if truthy(r.get("xiaomi_tc")) else []) if has(x))
    if proto: f.append(("Protocols", proto))
    f.append(("Port reset when plugging in", "Resets ports" if truthy(r.get("ports_reboot")) else "Stable"))
    if has(r.get("throttle_single_pass")):
        s = "Passed" if truthy(r.get("throttle_single_pass")) else dropped(r.get("throttle_single_drop"))
        t = temp(r.get("case_temp_single_c"))
        f.append(("Single-port test", s + (" · case " + t if t else "")))
    if has(r.get("throttle_total_pass")):
        s = "Passed" if truthy(r.get("throttle_total_pass")) else dropped(r.get("throttle_total_drop"))
        t = temp(r.get("case_temp_total_c"))
        f.append(("All-ports test", s + (" · case " + t if t else "")))
    f.append(("Form", "Corded" if truthy(r.get("corded")) else "Wall plug"))
    f.append(("Display", "Yes" if truthy(r.get("display")) else "No"))
    f.append(("Built-in cable", "Yes" if truthy(r.get("cable_in")) else "No"))
    f.append(("AC outlet", "Yes" if truthy(r.get("ac_outlet")) else "No"))
    if truthy(r.get("app_control")):
        f.append(("App control", r.get("app_detail") if has(r.get("app_detail")) and not re.fullmatch(r"(?i)true|false", r["app_detail"]) else "Yes"))
    return f


def facts_powerbanks(r):
    f = []
    if has(r.get("ports")): f.append(("Ports", r["ports"]))
    if num(r.get("rated")) or num(r.get("rated_wh")):
        f.append(("Rated capacity", " / ".join(x for x in ("%s mAh" % "{:,}".format(int(num(r["rated"]))) if num(r.get("rated")) else "",
                                                          "%s Wh" % fmt(num(r.get("rated_wh"))) if num(r.get("rated_wh")) else "") if x)))
    if num(r.get("real")): f.append(("Measured capacity", "%s Wh" % fmt(round(num(r["real"]), 1))))
    e5, em = first_num(r.get("eff5v")), first_num(r.get("eff_max"))
    if e5: f.append(("Efficiency at 5 V", "%s%%" % fmt(e5)))
    if em: f.append(("Efficiency at max power", "%s%%" % fmt(em)))
    if num(r.get("pwr")): f.append(("Power per port", "%s W" % fmt(num(r["pwr"]))))
    if num(r.get("total_pwr")): f.append(("Total output", "%s W" % fmt(num(r["total_pwr"]))))
    rip = curve_max(r.get("ripple_curve"), "mV")
    if rip: f.append(("Peak ripple", "%s mV" % fmt(rip)))
    if num(r.get("max_charge_pwr")): f.append(("Charging input", "%s W" % fmt(num(r["max_charge_pwr"]))))
    proto = ", ".join(x for x in [r.get("pd", "")] + [s.strip() for s in str(r.get("protocols_extra") or "").split(",")]
                      + (["Xiaomi Turbo Charge"] if truthy(r.get("xiaomi_tc")) else []) if has(x))
    if proto: f.append(("Protocols", proto))
    if has(r.get("throttle_single_pass")):
        s = "Passed" if truthy(r.get("throttle_single_pass")) else dropped(r.get("throttle_single_drop"))
        t = temp(r.get("case_temp_single_c"))
        f.append(("Max-power test", s + (" · case " + t if t else "")))
    if truthy(r.get("wireless_qi")):
        f.append(("Wireless charging", " · ".join(x for x in (r.get("qi_standard") or "Qi",
                                                              "%s W" % fmt(num(r["qi_pwr"])) if num(r.get("qi_pwr")) else "") if x)))
    f.append(("Display", "Yes" if truthy(r.get("display")) else "No"))
    f.append(("Built-in cable", "Yes" if truthy(r.get("cable_in")) else "No"))
    if truthy(r.get("app_control")): f.append(("App control", "Yes"))
    return f


FACTS = {"cables": facts_cables, "chargers": facts_chargers, "powerbanks": facts_powerbanks}


def headline(typ, r):
    """Short, truthful summary used in the meta description."""
    bits = []
    if typ == "cables":
        if num(r.get("pwr")): bits.append("%s W" % fmt(num(r["pwr"])))
        if num(r.get("res")): bits.append("%s mΩ resistance" % fmt(num(r["res"])))
        if has(r.get("spd_lbl")): bits.append(r["spd_lbl"])
    elif typ == "chargers":
        if num(r.get("total_pwr")): bits.append("%s W total" % fmt(num(r["total_pwr"])))
        eff = curve_max(r.get("eff_curve"), "%") or num(r.get("eff"))
        if eff: bits.append("%s%% peak efficiency" % fmt(round(eff, 1)))
        rip = curve_max(r.get("ripple_curve"), "mV") or num(r.get("ripple"))
        if rip: bits.append("%s mV ripple" % fmt(rip))
    else:
        if num(r.get("real")): bits.append("%s Wh measured" % fmt(round(num(r["real"]), 1)))
        if num(r.get("total_pwr")): bits.append("%s W output" % fmt(num(r["total_pwr"])))
        rip = curve_max(r.get("ripple_curve"), "mV")
        if rip: bits.append("%s mV ripple" % fmt(rip))
    return ", ".join(bits)


def shops(r):
    out = []
    def add(col, label):
        if has(r.get(col)) and r[col].strip() != "#":
            out.append((label, r[col].strip()))
    add("buy_am_us", "Amazon US"); add("buy_am_eu", "Amazon EU")
    if not (has(r.get("buy_am_us")) or has(r.get("buy_am_eu"))): add("buy_am", "Amazon")
    add("buy_ali", "AliExpress")
    add("buy_off_us", "Official store US"); add("buy_off_eu", "Official store EU")
    if not (has(r.get("buy_off_us")) or has(r.get("buy_off_eu"))): add("buy_off", "Official store")
    return [(l, u) for l, u in out if re.match(r"https?://", u)]


# ---------------------------------------------------------------- page
CSS = """
:root{--bg:#fff;--surface:#f6f8fa;--border:rgba(27,31,36,.12);--text:#1f2328;--text2:#59636e;--accent:#0860ca;--onaccent:#fff;--green:#177532;--gbg:rgba(23,117,50,.1);--orange:#8a5c00;--obg:rgba(138,92,0,.1);--red:#c4202b;--rbg:rgba(196,32,43,.08)}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--surface:#161b22;--border:rgba(255,255,255,.12);--text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;--onaccent:#0d1117;--green:#3fb950;--gbg:rgba(63,185,80,.13);--orange:#d29922;--obg:rgba(210,153,34,.13);--red:#f85149;--rbg:rgba(248,81,73,.13)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.crumbs a,footer a,.note a,.hub li a{text-decoration:underline}
.wrap{max-width:860px;margin:0 auto;padding:20px 18px 40px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:22px}
.logo{font-size:22px;font-weight:700;color:var(--text)}.logo b{color:var(--accent)}
.crumbs{font-size:13px;color:var(--text2);margin-bottom:6px}
h1{font-size:26px;line-height:1.25;margin:0 0 10px}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;background:var(--surface);border:1px solid var(--border);color:var(--text2)}
.chip.good{background:var(--gbg);color:var(--green);border-color:transparent}.chip.warn{background:var(--obg);color:var(--orange);border-color:transparent}.chip.bad{background:var(--rbg);color:var(--red);border-color:transparent}
.note{color:var(--text2);margin:0 0 18px}
.hero{display:block;max-width:100%;max-height:360px;margin:0 0 20px;border-radius:10px;border:1px solid var(--border);background:var(--surface)}
dl{margin:0 0 22px;border:1px solid var(--border);border-radius:10px;overflow:hidden}
.row{display:grid;grid-template-columns:minmax(120px,38%) 1fr;gap:12px;padding:9px 14px;border-top:1px solid var(--border)}
.row:first-child{border-top:0}.row:nth-child(even){background:var(--surface)}
dt{color:var(--text2);font-size:13.5px}dd{margin:0;font-weight:600}
.btns{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 24px}
.btn{display:inline-block;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:14px;font-weight:600}
.btn.primary{background:var(--accent);color:var(--onaccent);border-color:var(--accent)}
.btn:hover{text-decoration:none;filter:brightness(1.07)}
h2{font-size:16px;margin:26px 0 8px}ul.more{margin:0;padding-left:20px}ul.more li{margin:3px 0}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--border);color:var(--text2);font-size:12.5px}
@media(max-width:560px){.row{grid-template-columns:1fr;gap:2px}h1{font-size:22px}}
"""


def verdict_class(v):
    v = (v or "").strip().lower()
    return "good" if v in ("excellent", "good") else ("warn" if v == "caution" else ("bad" if v == "poor" else ""))


def render(typ, r, sl, siblings):
    name = r["name"].strip()
    label = TYPE_LABEL[typ]
    url = "%s/d/%s/%s/" % (SITE, typ, sl)
    title = "%s – %s test results | WhatWatts" % (name, label)
    summ = headline(typ, r)
    vn = r.get("vn", "").strip() if has(r.get("vn")) else ""
    if vn.lower() in ((r.get("value") or "").strip().lower(), (r.get("v") or "").strip().lower()):
        vn = ""
    desc = ("%s %s test: %s. %s" % (name, label, summ, vn)).replace(": .", ".").strip()
    desc = re.sub(r"\s+", " ", desc)
    if len(desc) > 158:
        desc = desc[:155].rsplit(" ", 1)[0] + "…"
    ext = photo_urls(r, "exterior", typ)
    img = ext[0] if ext else SITE + "/social-preview.png"
    facts = "".join('<div class="row"><dt>%s</dt><dd>%s</dd></div>' % (E(k), E(str(v))) for k, v in FACTS[typ](r) if has(v))
    chips = []
    if has(r.get("brand")): chips.append('<span class="chip">%s</span>' % E(r["brand"]))
    if has(r.get("v")): chips.append('<span class="chip %s">Verdict: %s</span>' % (verdict_class(r["v"]), E(r["v"])))
    if num(r.get("price")): chips.append('<span class="chip">$%s at review%s</span>' % (fmt(num(r["price"])), (" · " + E(r["value"])) if has(r.get("value")) else ""))
    btns = ['<a class="btn primary" href="%s/#%s/%s">Open in the full database</a>' % (SITE, typ, sl)]
    if has(r.get("yt")) and re.match(r"https?://", r["yt"]): btns.append('<a class="btn" href="%s" target="_blank" rel="noopener">Watch the review</a>' % E(r["yt"], True))
    for lab, u in shops(r):
        btns.append('<a class="btn" href="%s" target="_blank" rel="nofollow sponsored noopener">Buy: %s</a>' % (E(u, True), E(lab)))
    others = "".join('<li><a href="%s/d/%s/%s/">%s</a></li>' % (SITE, typ, s, E(n)) for n, s in siblings)
    more = ("<h2>More %s tests</h2><ul class=\"more\">%s</ul>" % (label, others)) if others else ""
    ld = ('{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":['
          '{"@type":"ListItem","position":1,"name":"WhatWatts","item":"%s/"},'
          '{"@type":"ListItem","position":2,"name":"%s","item":"%s/#%s"},'
          '{"@type":"ListItem","position":3,"name":%s,"item":"%s"}]}') % (
        SITE, TYPE_PLURAL[typ], SITE, typ, __import__("json").dumps(name), url)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc, True)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:site_name" content="WhatWatts">
<meta property="og:title" content="{E(title, True)}"><meta property="og:description" content="{E(desc, True)}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{E(img, True)}">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.ico"><link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<style>{CSS}</style>
<script type="application/ld+json">{ld}</script>
</head><body><div class="wrap">
<header class="top"><a class="logo" href="/">What<b>Watts</b></a><a href="/#{typ}">All {TYPE_PLURAL[typ].lower()} →</a></header>
<main>
<div class="crumbs"><a href="/">WhatWatts</a> › <a href="/#{typ}">{TYPE_PLURAL[typ]}</a></div>
<h1>{E(name)}: {label} test results</h1>
<div class="meta">{"".join(chips)}</div>
{('<p class="note">%s</p>' % E(vn)) if vn else ''}
{('<img class="hero" src="%s" alt="%s" loading="lazy" onerror="this.remove()">' % (E(ext[0], True), E(name + " exterior", True))) if ext else ''}
<dl>{facts}</dl>
<div class="btns">{"".join(btns)}</div>
{more}
</main>
<footer>Measured by WhatWatts on a retail unit, with the procedure described on the <a href="/#method">How I test</a> page; see also the <a href="/best/">rankings</a>. This page contains affiliate links: I may earn a commission from qualifying purchases at no extra cost to you. Prices reflect the cost at the time of testing and may have changed since.</footer>
</div>
<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{{"token": "{BEACON}"}}'></script>
</body></html>
"""



# ---------------------------------------------------------------- rankings
RANK_CSS = """
.rank-wrap{overflow-x:auto;margin:0 0 22px;border:1px solid var(--border);border-radius:10px}
table.rank{width:100%;border-collapse:collapse;font-size:14px;min-width:560px}
table.rank th,table.rank td{padding:9px 12px;text-align:left;border-top:1px solid var(--border);vertical-align:top}
table.rank thead th{border-top:0;background:var(--surface);color:var(--text2);font-size:12.5px;font-weight:600;white-space:nowrap}
table.rank td.n{color:var(--text2);width:34px}table.rank td.key,table.rank th.key{font-weight:700}
table.rank td.key{color:var(--accent)}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.hub h2{margin-top:24px}.hub ul{margin:6px 0 0;padding-left:20px}.hub li{margin:4px 0}
"""
MIN_ROWS, MAX_ROWS = 3, 30


def _eff_c(r):  return curve_max(r.get("eff_curve"), "%") or num(r.get("eff"))
def _rip_c(r):  return curve_max(r.get("ripple_curve"), "mV") or num(r.get("ripple"))
def _pos(x):    return x if (x is not None and x > 0) else None
def _tp(r):     return num(r.get("total_pwr")) or num(r.get("pwr"))
def _w(x):      return ("%s W" % fmt(x)) if x else ""
def _pct(x):    return ("%s%%" % fmt(round(x, 1))) if x else ""
def _mv(x):     return ("%s mV" % fmt(x)) if x else ""
def _c(x):      return temp(x)
def _price(r):  return ("$%s" % fmt(num(r["price"]))) if num(r.get("price")) else ""
def _verdict(r): return r.get("v", "") if has(r.get("v")) else ""

RANKINGS = [
    # key, type, title (h1), short, intro, value(row)->number, higher_is_better, row filter, value formatter, extra columns [(header, fn)]
    ("chargers-efficiency", "chargers", "Most efficient USB-C chargers", "chargers ranked by measured peak efficiency",
     "Peak efficiency is the best value measured across the tested voltage steps with the load connected. Higher means less energy is wasted as heat.",
     lambda r: _pos(_eff_c(r)), True, None, _pct,
     [("Total power", lambda r: _w(_tp(r))), ("Peak ripple", lambda r: _mv(_pos(_rip_c(r)))), ("Verdict", _verdict), ("Price", _price)]),
    ("chargers-lowest-ripple", "chargers", "USB-C chargers with the lowest output ripple", "chargers ranked by measured peak output ripple",
     "Ripple is the electrical noise on the output, measured with an oscilloscope. Lower is cleaner; peak value across the tested voltage steps is shown.",
     lambda r: _pos(_rip_c(r)), False, None, _mv,
     [("Total power", lambda r: _w(_tp(r))), ("Peak efficiency", lambda r: _pct(_pos(_eff_c(r)))), ("Verdict", _verdict), ("Price", _price)]),
    ("chargers-coolest", "chargers", "Chargers that stay coolest under load", "chargers ranked by case temperature in the single-port load test",
     "Case temperature after the 30-minute load test on one port. Compare chargers of similar output power: a 20 W charger naturally runs cooler than a 140 W one.",
     lambda r: _pos(num(r.get("case_temp_single_c"))), False, None, lambda x: temp(x),
     [("Output in the test", lambda r: _w(num(r.get("pwr")))), ("Throttled?", lambda r: "No" if truthy(r.get("throttle_single_pass")) else ("Yes" if has(r.get("throttle_single_pass")) else "")),
      ("Verdict", _verdict), ("Price", _price)]),
    ("chargers-up-to-65w-efficiency", "chargers", "Most efficient chargers up to 65 W", "chargers up to 65 W ranked by measured peak efficiency",
     "Chargers with up to 65 W of total power, ranked by measured peak efficiency.",
     lambda r: _pos(_eff_c(r)), True, lambda r: (_tp(r) or 0) <= 65, _pct,
     [("Total power", lambda r: _w(_tp(r))), ("Peak ripple", lambda r: _mv(_pos(_rip_c(r)))), ("Verdict", _verdict), ("Price", _price)]),
    ("chargers-66-100w-efficiency", "chargers", "Most efficient 66–100 W chargers", "chargers from 66 to 100 W ranked by measured peak efficiency",
     "Chargers with 66–100 W of total power, ranked by measured peak efficiency.",
     lambda r: _pos(_eff_c(r)), True, lambda r: 65 < (_tp(r) or 0) <= 100, _pct,
     [("Total power", lambda r: _w(_tp(r))), ("Peak ripple", lambda r: _mv(_pos(_rip_c(r)))), ("Verdict", _verdict), ("Price", _price)]),
    ("chargers-over-100w-efficiency", "chargers", "Most efficient chargers over 100 W", "chargers over 100 W ranked by measured peak efficiency",
     "Chargers with more than 100 W of total power, ranked by measured peak efficiency.",
     lambda r: _pos(_eff_c(r)), True, lambda r: (_tp(r) or 0) > 100, _pct,
     [("Total power", lambda r: _w(_tp(r))), ("Peak ripple", lambda r: _mv(_pos(_rip_c(r)))), ("Verdict", _verdict), ("Price", _price)]),

    ("cables-lowest-resistance", "cables", "USB-C cables with the lowest resistance", "cables ranked by measured resistance",
     "Resistance is measured on the whole cable, connectors included. Lower resistance means less voltage drop and less heat at high current.",
     lambda r: _pos(num(r.get("res"))), False, None, lambda x: "%s mΩ" % fmt(x),
     [("Rated power", lambda r: _w(num(r.get("pwr")))), ("Data speed", lambda r: r.get("spd_lbl", "") if has(r.get("spd_lbl")) else ""), ("Verdict", _verdict), ("Price", _price)]),
    ("cables-240w-resistance", "cables", "240 W cables ranked by resistance", "240 W cables ranked by measured resistance",
     "Cables rated for 240 W, ranked by measured resistance (lowest first).",
     lambda r: _pos(num(r.get("res"))), False, lambda r: (num(r.get("pwr")) or 0) >= 240, lambda x: "%s mΩ" % fmt(x),
     [("Data speed", lambda r: r.get("spd_lbl", "") if has(r.get("spd_lbl")) else ""), ("Verdict", _verdict), ("Price", _price)]),
    ("cables-100w-resistance", "cables", "100 W cables ranked by resistance", "100 W cables ranked by measured resistance",
     "Cables rated for 100 W (below 240 W), ranked by measured resistance (lowest first).",
     lambda r: _pos(num(r.get("res"))), False, lambda r: 100 <= (num(r.get("pwr")) or 0) < 240, lambda x: "%s mΩ" % fmt(x),
     [("Data speed", lambda r: r.get("spd_lbl", "") if has(r.get("spd_lbl")) else ""), ("Verdict", _verdict), ("Price", _price)]),
    ("cables-fastest", "cables", "Fastest USB-C cables (measured data speed)", "cables ranked by measured data speed",
     "Data speed measured on a real link. My test equipment tops out at 20 Gbps, so faster cables are shown at that limit; ties are ordered by lower resistance.",
     lambda r: _pos(num(r.get("spd"))), True, None, lambda x: "%s Gbps" % fmt(x),
     [("Advertised", lambda r: ("%s Gbps" % fmt(num(r["adv_spd"]))) if num(r.get("adv_spd")) else ""), ("Resistance", lambda r: ("%s mΩ" % fmt(num(r["res"]))) if num(r.get("res")) else ""),
      ("Verdict", _verdict), ("Price", _price)]),

    ("powerbanks-capacity", "powerbanks", "Power banks with the highest real capacity", "power banks ranked by measured capacity in Wh",
     "Capacity actually delivered in the discharge test, in watt-hours. The rated capacity is shown next to it for comparison.",
     lambda r: _pos(num(r.get("real"))), True, None, lambda x: "%s Wh" % fmt(round(x, 1)),
     [("Rated", lambda r: ("%s Wh" % fmt(num(r["rated_wh"]))) if num(r.get("rated_wh")) else ""), ("Output", lambda r: _w(_tp(r))), ("Verdict", _verdict), ("Price", _price)]),
    ("powerbanks-efficiency", "powerbanks", "Most efficient power banks (at maximum power)", "power banks ranked by efficiency at maximum power",
     "Efficiency = energy delivered divided by capacity, measured when discharging at the maximum power of a single port. Values are shown as entered in the test table.",
     lambda r: _pos(first_num(r.get("eff_max"))), True, None, lambda x: "%s%%" % fmt(x),
     [("Measured", lambda r: ("%s Wh" % fmt(round(num(r["real"]), 1))) if num(r.get("real")) else ""), ("Output", lambda r: _w(_tp(r))), ("Verdict", _verdict), ("Price", _price)]),
    ("powerbanks-lowest-ripple", "powerbanks", "Power banks with the lowest output ripple", "power banks ranked by measured peak output ripple",
     "Ripple is the electrical noise on the output, measured with an oscilloscope. Lower is cleaner; the peak value across the tested steps is shown.",
     lambda r: _pos(curve_max(r.get("ripple_curve"), "mV")), False, None, _mv,
     [("Measured", lambda r: ("%s Wh" % fmt(round(num(r["real"]), 1))) if num(r.get("real")) else ""), ("Output", lambda r: _w(_tp(r))), ("Verdict", _verdict), ("Price", _price)]),
    ("powerbanks-output", "powerbanks", "Power banks with the highest output power", "power banks ranked by total output power",
     "Maximum total output power of the power bank.",
     lambda r: _pos(num(r.get("total_pwr"))), True, None, lambda x: "%s W" % fmt(x),
     [("Measured", lambda r: ("%s Wh" % fmt(round(num(r["real"]), 1))) if num(r.get("real")) else ""), ("Per port", lambda r: _w(num(r.get("pwr")))), ("Verdict", _verdict), ("Price", _price)]),
]


def head_common(title, desc, url, extra_css=""):
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc, True)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:site_name" content="WhatWatts">
<meta property="og:title" content="{E(title, True)}"><meta property="og:description" content="{E(desc, True)}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{SITE}/social-preview.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.ico"><link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<style>{CSS}{extra_css}</style>
"""


FOOT = ('<footer>Measured by WhatWatts on retail units, with the procedure described on the <a href="/#method">How I test</a> page. '
        'This site contains affiliate links: I may earn a commission from qualifying purchases at no extra cost to you. '
        'Prices reflect the cost at the time of testing and may have changed since.</footer>')
BEACON_TAG = ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon=\'{"token": "%s"}\'></script>' % BEACON)


def build_ranking(rk, items_by_type):
    key, typ, h1, short, intro, valf, higher, flt, vfmt, cols = rk
    rows = []
    for r, sl in items_by_type.get(typ, []):
        if flt and not flt(r):
            continue
        v = valf(r)
        if v is None:
            continue
        rows.append((v, r, sl))
    if len(rows) < MIN_ROWS:
        return None
    tie = (lambda t: (num(t[1].get("res")) or 1e9)) if key == "cables-fastest" else (lambda t: 0)
    rows.sort(key=lambda t: ((-t[0]) if higher else t[0], tie(t), t[1]["name"].casefold()))
    total = len(rows); rows = rows[:MAX_ROWS]
    url = "%s/best/%s/" % (SITE, key)
    top = rows[0]
    title = "%s – tested ranking | WhatWatts" % h1
    desc = "%d %s. Top: %s (%s)." % (total, short, top[1]["name"], vfmt(top[0]))
    label = ("Case temperature" if "coolest" in key else "Resistance" if "resistance" in key else "Measured speed" if "fastest" in key
             else "Measured capacity" if "capacity" in key else "Output power" if key.endswith("output") else "Peak ripple" if "ripple" in key
             else "Peak efficiency" if typ == "chargers" else "Efficiency at max power")
    heads = "".join('<th scope="col"%s>%s</th>' % (' class="key"' if i == 2 else "", E(h)) for i, h in enumerate(["#", "Device", label] + [c[0] for c in cols]))
    body = []
    for i, (v, r, sl) in enumerate(rows, 1):
        tds = ['<td class="n">%d</td>' % i,
               '<td><a href="%s/d/%s/%s/">%s</a></td>' % (SITE, typ, sl, E(r["name"])),
               '<td class="key">%s</td>' % E(vfmt(v))]
        tds += ["<td>%s</td>" % E(c[1](r) or "–") for c in cols]
        body.append("<tr>%s</tr>" % "".join(tds))
    ld = ('{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":['
          '{"@type":"ListItem","position":1,"name":"WhatWatts","item":"%s/"},'
          '{"@type":"ListItem","position":2,"name":"Rankings","item":"%s/best/"},'
          '{"@type":"ListItem","position":3,"name":%s,"item":"%s"}]}') % (SITE, SITE, __import__("json").dumps(h1), url)
    page = head_common(title, desc, url, RANK_CSS) + f"""<script type="application/ld+json">{ld}</script>
</head><body><div class="wrap">
<header class="top"><a class="logo" href="/">What<b>Watts</b></a><a href="/best/">All rankings →</a></header>
<main>
<div class="crumbs"><a href="/">WhatWatts</a> › <a href="/best/">Rankings</a></div>
<h1>{E(h1)}</h1>
<p class="note">{E(intro)} {total} tested {E(TYPE_LABEL[typ])}s are in this list{'; the top %d are shown.' % MAX_ROWS if total > MAX_ROWS else '.'}</p>
<div class="rank-wrap"><table class="rank"><caption class="sr-only">{E(h1)}</caption><thead><tr>{heads}</tr></thead><tbody>{''.join(body)}</tbody></table></div>
<div class="btns"><a class="btn primary" href="{SITE}/#{typ}">Open the full {E(TYPE_LABEL[typ])} database</a></div>
</main>
{FOOT}
</div>{BEACON_TAG}
</body></html>
"""
    return {"key": key, "typ": typ, "h1": h1, "short": short, "n": total, "url": url, "html": page}


def build_hub(made):
    url = SITE + "/best/"
    title = "Tested USB charger, cable and power bank rankings | WhatWatts"
    desc = "Rankings built from real measurements: efficiency, ripple, resistance, speed, capacity and heat for USB chargers, cables and power banks."
    sections = []
    for typ in ("chargers", "cables", "powerbanks"):
        li = "".join('<li><a href="%s">%s</a> <span style="color:var(--text2)">(%d tested)</span></li>' % (m["url"], E(m["h1"]), m["n"]) for m in made if m["typ"] == typ)
        if li:
            sections.append("<h2>%s</h2><ul>%s</ul>" % (TYPE_PLURAL[typ], li))
    return head_common(title, desc, url, RANK_CSS) + f"""</head><body><div class="wrap hub">
<header class="top"><a class="logo" href="/">What<b>Watts</b></a><a href="/">Full database →</a></header>
<main>
<div class="crumbs"><a href="/">WhatWatts</a> › Rankings</div>
<h1>Rankings from real measurements</h1>
<p class="note">Every list is sorted by one measured value, so you can see exactly what the order is based on. Nothing here is a made-up score.</p>
{''.join(sections)}
</main>
{FOOT}
</div>{BEACON_TAG}
</body></html>
"""


# ---------------------------------------------------------------- main
def load_rows(url):
    header, data = parse(download(url))
    return [dict(zip(header, [c.strip() for c in row])) for row in data if row and row[0].strip()]


def old_lastmods(sitemap_path):
    try:
        t = open(sitemap_path, encoding="utf-8").read()
    except OSError:
        return {}
    return {m.group(1): m.group(2) for m in re.finditer(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", t)}


def main(index_path="index.html", out="."):
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    sm_path = os.path.join(out, "sitemap.xml")
    lastmods = old_lastmods(sm_path)
    urls, failed, written, unchanged = [], [], 0, 0
    items_by_type = {}
    for typ, src in sheet_urls(index_path).items():
        base = os.path.join(out, "d", typ)
        try:
            rows = load_rows(src)
            if not rows:
                raise ValueError("no rows")
        except Exception as e:
            failed.append(typ)
            print("FAIL %-11s %s (old pages kept)" % (typ, e))
            for sl in (os.listdir(base) if os.path.isdir(base) else []):      # keep them in the sitemap
                urls.append("%s/d/%s/%s/" % (SITE, typ, sl))
            continue
        rows = [r for r in rows if has(r.get("name"))]
        rows.sort(key=lambda r: r["name"].casefold())                          # same order as the site, so duplicate slugs match
        seen, items = set(), []
        for r in rows:
            sl = base_sl = slug(r["name"]) or "item"; i = 2
            while sl in seen:
                sl = "%s-%d" % (base_sl, i); i += 1
            seen.add(sl); items.append((r, sl))
        for r, sl in items:
            same_brand = [(o["name"], s) for o, s in items if s != sl and has(r.get("brand")) and o.get("brand") == r.get("brand")]
            rest = [(o["name"], s) for o, s in items if s != sl and (o["name"], s) not in same_brand]
            siblings = (same_brand + rest)[:6]
            page = render(typ, r, sl, siblings)
            path = os.path.join(base, sl, "index.html")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            u = "%s/d/%s/%s/" % (SITE, typ, sl)
            if old != page:
                open(path, "w", encoding="utf-8", newline="\n").write(page); written += 1
                lastmods[u] = today
            else:
                unchanged += 1
                lastmods.setdefault(u, today)
            urls.append(u)
        # remove pages of devices that disappeared from the sheet (only after a good download)
        items_by_type[typ] = items
        keep = {sl for _, sl in items}
        for sl in (os.listdir(base) if os.path.isdir(base) else []):
            if sl not in keep and len(items) >= 0.5 * max(1, len(os.listdir(base))):
                shutil.rmtree(os.path.join(base, sl))
        print("OK   %-11s %d pages" % (typ, len(items)))
    # rankings (only when every sheet was downloaded; otherwise the old ones stay)
    best_dir = os.path.join(out, "best")
    if failed:
        for sl in (os.listdir(best_dir) if os.path.isdir(best_dir) else []):
            urls.append("%s/best/%s/" % (SITE, sl) if sl != "index.html" else SITE + "/best/")
    else:
        made = [m for m in (build_ranking(rk, items_by_type) for rk in RANKINGS) if m]
        pages = [(os.path.join(best_dir, m["key"], "index.html"), m["html"], m["url"]) for m in made]
        if made:
            pages.append((os.path.join(best_dir, "index.html"), build_hub(made), SITE + "/best/"))
        for path, page, u in pages:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if old != page:
                open(path, "w", encoding="utf-8", newline="\n").write(page); written += 1; lastmods[u] = today
            else:
                unchanged += 1; lastmods.setdefault(u, today)
            urls.append(u)
        keep_dirs = {m["key"] for m in made}
        for sl in (os.listdir(best_dir) if os.path.isdir(best_dir) else []):
            if os.path.isdir(os.path.join(best_dir, sl)) and sl not in keep_dirs:
                shutil.rmtree(os.path.join(best_dir, sl))
        print("OK   rankings    %d pages + hub" % len(made))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
             "  <url>\n    <loc>%s/</loc>\n    <changefreq>weekly</changefreq>\n    <priority>1.0</priority>\n  </url>" % SITE]
    for u in sorted(set(urls)):
        lines.append("  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n    <priority>%s</priority>\n  </url>" % (u, lastmods.get(u, today), "0.7" if "/best/" in u else "0.6"))
    lines.append("</urlset>\n")
    open(sm_path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    print("Pages written: %d, unchanged: %d, in sitemap: %d" % (written, unchanged, len(set(urls)) + 1))
    if failed:
        sys.exit("Some sheets failed: " + ", ".join(failed))


if __name__ == "__main__":
    main(*sys.argv[1:3])
