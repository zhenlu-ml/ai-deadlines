# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""Rebuild data/deadlines.js from upstream deadline trackers + official sites.

Pipeline
  1. Pull two community-maintained trackers (single tarball each):
       - ccfddl/ccf-deadlines        (updated almost daily)
       - huggingface/ai-deadlines    (AI-focused, richer metadata)
  2. Keep only conferences listed in catalog.yaml, merge editions by (conf, year).
  3. For every upcoming deadline, fetch the conference's official website / CFP
     page and check whether the deadline date actually appears there.
       verified  -> the date is printed on the official page
       conflict  -> the two trackers disagree; the one found on the official
                    page wins, otherwise ccfddl (fresher) wins and it is flagged
       unverified-> page reachable but date not found (often on a sub-page)
  4. overrides.yaml (hand-written) is applied last and always wins.

Usage:  uv run scripts/update.py            # full run
        uv run scripts/update.py --no-verify  # skip official-site checks
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import io
import json
import re
import ssl
import sys
import tarfile
import urllib.request
from urllib.parse import urljoin, urlparse
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CCF_TAR = "https://codeload.github.com/ccfddl/ccf-deadlines/tar.gz/main"
HF_TAR = "https://codeload.github.com/huggingface/ai-deadlines/tar.gz/main"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)

SUBMISSION_TYPES = {"abstract", "paper", "submission", "full_paper"}


def log(*a):
    print(*a, file=sys.stderr)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --------------------------------------------------------------------------- time
TZ_ABBR = {"AOE": -12, "PST": -8, "PDT": -7, "MST": -7, "MDT": -6, "CST": -6, "CDT": -5,
           "EST": -5, "EDT": -4, "UTC": 0, "GMT": 0, "Z": 0, "CET": 1, "CEST": 2,
           "BST": 1, "EET": 2, "EEST": 3, "IST": 5.5, "KST": 9, "JST": 9}


def parse_tz(tz) -> dt.tzinfo:
    if tz is None or str(tz).strip() == "":
        return dt.timezone(dt.timedelta(hours=-12))  # AoE is the de-facto default
    s = str(tz).strip()
    m = re.fullmatch(r"(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", s, re.I)
    if m:
        sign = 1 if m[1] == "+" else -1
        return dt.timezone(sign * dt.timedelta(hours=int(m[2]), minutes=int(m[3] or 0)))
    if s.upper() in TZ_ABBR:
        return dt.timezone(dt.timedelta(hours=TZ_ABBR[s.upper()]))
    try:
        return ZoneInfo(s)
    except Exception:
        if s.upper() in ("PT", "PACIFIC"):
            return ZoneInfo("America/Los_Angeles")
        if s.upper() in ("ET", "EASTERN"):
            return ZoneInfo("America/New_York")
        log(f"  ! unknown timezone {s!r}, assuming AoE")
        return dt.timezone(dt.timedelta(hours=-12))


def tz_label(tz) -> str:
    if tz is None or str(tz).strip() == "":
        return "AoE"
    s = str(tz).strip()
    return "AoE" if s.upper() in ("AOE", "UTC-12", "UTC-12:00", "GMT-12") else s


def parse_when(value, tz) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, dt.date):
        d = dt.datetime.combine(value, dt.time(23, 59, 59))
    else:
        s = str(value).strip()
        if not s or s.upper() in ("TBD", "TBA", "N/A"):
            return None
        d = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                d = dt.datetime.strptime(s, fmt)
                if fmt == "%Y-%m-%d":
                    d = d.replace(hour=23, minute=59, second=59)
                break
            except ValueError:
                pass
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=parse_tz(tz))
    return d


# --------------------------------------------------------------------------- fetch
def http_get(url: str, timeout=20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except ssl.SSLError:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=timeout, context=ctx).read()


def iter_yaml(tar_url: str, path_re: str):
    log(f"↓ {tar_url}")
    t = tarfile.open(fileobj=io.BytesIO(http_get(tar_url, timeout=90)), mode="r:gz")
    for m in t.getmembers():
        if m.isfile() and re.search(path_re, m.name):
            try:
                yield m.name, yaml.safe_load(t.extractfile(m).read()) or []
            except yaml.YAMLError as e:
                log(f"  ! bad yaml {m.name}: {e}")


# --------------------------------------------------------------------------- sources
def load_ccf(resolve):
    out = []
    for name, data in iter_yaml(CCF_TAR, r"/conference/[A-Z]+/[^/]+\.yml$"):
        for conf in data if isinstance(data, list) else []:
            key = resolve(conf.get("title", ""))
            if not key:
                continue
            rank = conf.get("rank") or {}
            for ed in conf.get("confs") or []:
                tz = ed.get("timezone")
                dls = []
                for i, tl in enumerate(ed.get("timeline") or []):
                    note = (tl.get("comment") or "").strip()
                    if (a := parse_when(tl.get("abstract_deadline"), tz)):
                        dls.append(dict(type="abstract", label="Abstract" + (f" · {note}" if note else ""),
                                        raw=str(tl.get("abstract_deadline")), when=a, tz=tz_label(tz)))
                    if (p := parse_when(tl.get("deadline"), tz)):
                        dls.append(dict(type="paper", label=note or "Paper submission",
                                        raw=str(tl.get("deadline")), when=p, tz=tz_label(tz)))
                out.append(dict(
                    key=key, year=int(ed["year"]), title=conf.get("title"),
                    full_name=conf.get("description"), link=ed.get("link"),
                    date=ed.get("date"), place=ed.get("place"),
                    ranks={k: v for k, v in (("CCF", rank.get("ccf")), ("CORE", rank.get("core")), ("THCPL", rank.get("thcpl"))) if v and v != "N"},
                    deadlines=dls, source="ccfddl"))
    return out


HF_TYPE = {"abstract": "abstract", "abstract_deadline": "abstract", "paper": "paper",
           "submission": "paper", "full_paper": "paper", "deadline": "paper"}


def load_hf(resolve):
    out = []
    for name, data in iter_yaml(HF_TAR, r"/src/data/conferences/[^/]+\.yml$"):
        for ed in data if isinstance(data, list) else []:
            key = resolve(ed.get("title", ""))
            if not key or not ed.get("year"):
                continue
            top_tz = ed.get("timezone")
            dls = []
            items = ed.get("deadlines")
            if items is None:  # legacy flat format
                items = [{"type": k, "date": ed[k], "timezone": top_tz} for k in ("abstract_deadline", "deadline") if ed.get(k)]
            for d in items or []:
                typ = HF_TYPE.get(str(d.get("type", "")).lower())
                if not typ:
                    continue
                tz = d.get("timezone") or top_tz
                if (w := parse_when(d.get("date"), tz)):
                    label = d.get("label") or ("Abstract" if typ == "abstract" else "Paper submission")
                    dls.append(dict(type=typ, label=label, raw=str(d.get("date")), when=w, tz=tz_label(tz)))
            ranks = {}
            for part in str(ed.get("rankings") or "").split(","):
                if ":" in part:
                    k, v = (x.strip() for x in part.split(":", 1))
                    if v and v.upper() not in ("N", "NONE", "N/A"):
                        ranks[k.upper()] = v
            place = ed.get("venue") or ", ".join(x for x in (ed.get("city"), ed.get("country")) if x) or None
            out.append(dict(
                key=key, year=int(ed["year"]), title=ed.get("title"), full_name=ed.get("full_name"),
                link=ed.get("link"), date=ed.get("date"), place=place, ranks=ranks,
                deadlines=dls, source="huggingface", note=ed.get("note")))
    return out


# --------------------------------------------------------------------------- official-site verification
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


def page_text(raw: bytes) -> str:
    s = raw.decode("utf-8", "ignore")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"(?<=\d)\s*(st|nd|rd|th)\b", "", s, flags=re.I)  # 16th -> 16
    return re.sub(r"\s+", " ", s).lower()


def date_patterns(d: dt.date) -> list[re.Pattern]:
    full = MONTHS[d.month - 1]
    sept = r"|sept\.?" if d.month == 9 else ""
    mon = rf"(?:{full}|{full[:3]}\.?{sept})"
    day = rf"0?{d.day}"
    wk = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    return [re.compile(p) for p in (
        rf"\b{mon}\s+{day}\b",                                  # Nov 16 / November 16, 2026
        rf"\b{day}\s+(?:of\s+)?{mon}\b",                        # 16 November
        rf"\b{wk}{day}\s*[-–]\s*{mon}\b",                       # 16-Nov
        rf"\b{d.year}[-/.]0?{d.month}[-/.]0?{d.day}\b",         # 2026-11-16
        rf"\b0?{d.month}/0?{d.day}/(?:{d.year}|{d.year % 100})\b",  # 11/16/2026
        rf"\b0?{d.day}[./]0?{d.month}[./]{d.year}\b",           # 16.11.2026
    )]


_page_cache: dict[str, str | None] = {}
_page_links: dict[str, list[str]] = {}
LINK_HINT = re.compile(r"date|call|cfp|paper|submi|author|deadline", re.I)


def fetch_text(url: str) -> str | None:
    if url not in _page_cache:
        try:
            raw = http_get(url)
            _page_cache[url] = page_text(raw)
            _page_links[url] = sub_links(url, raw.decode("utf-8", "ignore"))
        except Exception as e:
            log(f"  · fetch failed {url}: {type(e).__name__}")
            _page_cache[url] = None
    return _page_cache[url]


def sub_links(base: str, s: str, limit=6) -> list[str]:
    """Same-site links whose text/href looks like 'Important dates' / 'Call for papers'."""
    host = urlparse(base).netloc.split(":")[0].removeprefix("www.")
    out = []
    for href, text in re.findall(r"""(?is)<a\s[^>]*?href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", s):
        text = re.sub(r"<[^>]+>", " ", text)
        if not LINK_HINT.search(text + " " + href) or href.startswith(("mailto:", "javascript:")):
            continue
        u = urljoin(base, href.strip())
        h = urlparse(u).netloc.split(":")[0].removeprefix("www.")
        if (h == host or h.endswith("." + host) or host.endswith("." + h)) and u not in out and u != base \
                and not re.search(r"\.(pdf|zip|png|jpe?g|docx?)$", u, re.I):
            out.append(u)
    # prefer the most specific hints first
    out.sort(key=lambda u: 0 if re.search(r"date|deadline", u, re.I) else 1 if re.search(r"call|cfp", u, re.I) else 2)
    return out[:limit]


def candidate_urls(ed, catalog_entry) -> list[str]:
    urls = []
    if catalog_entry.get("cfp"):
        urls.append(catalog_entry["cfp"].format(year=ed["year"]))
    for link in ed.get("links", []):
        if link and link not in urls:
            urls.append(link)
    return urls


def found_on(texts: list[str], raw: str, when: dt.datetime) -> bool:
    # Match the date as written by the conference (in its own timezone), not the UTC date.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    d = dt.date(int(m[1]), int(m[2]), int(m[3])) if m else when.date()
    pats = date_patterns(d)
    return any(p.search(t) for t in texts for p in pats)


# --------------------------------------------------------------------------- merge
def same_time(a: dt.datetime, b: dt.datetime) -> bool:
    return abs((a - b).total_seconds()) <= 3 * 3600


def merge(records, catalog, verify: bool):
    eds: dict[tuple, dict] = {}
    for r in records:
        k = (r["key"], r["year"])
        e = eds.setdefault(k, dict(key=r["key"], year=r["year"], by_source={}, links=[]))
        e["by_source"][r["source"]] = r
        if r.get("link") and r["link"] not in e["links"]:
            e["links"].append(r["link"])

    # decide which editions need checking against the official site
    horizon = NOW - dt.timedelta(days=3)
    live = [e for e in eds.values()
            if any(d["when"] >= horizon for r in e["by_source"].values() for d in r["deadlines"])]
    page_urls = {id(e): candidate_urls(e, catalog[e["key"]]) for e in live}

    def run(urls):
        with cf.ThreadPoolExecutor(16) as ex:
            list(ex.map(fetch_text, sorted(set(urls) - set(_page_cache))))

    if verify and live:
        first = [u for us in page_urls.values() for u in us]
        log(f"⟳ checking {len(set(first))} official pages …")
        run(first)
        # pass 2: dates are often on an 'Important dates' / 'Call for papers' sub-page
        second = []
        for e in live:
            texts = [t for u in page_urls[id(e)] if (t := _page_cache.get(u))]
            dls = [d for r in e["by_source"].values() for d in r["deadlines"] if d["when"] >= horizon]
            if not all(found_on(texts, d["raw"], d["when"]) for d in dls):
                extra = [l for u in page_urls[id(e)] for l in _page_links.get(u, [])][:6]
                page_urls[id(e)] += extra
                second += extra
        if second:
            log(f"⟳ following {len(set(second))} 'dates / call for papers' sub-pages …")
            run(second)

    result = []
    for (key, year), e in sorted(eds.items()):
        src = e["by_source"]
        ccf, hf = src.get("ccfddl"), src.get("huggingface")
        primary = ccf or hf
        cat = catalog[key]
        texts = [t for u in page_urls.get(id(e), []) if (t := _page_cache.get(u))]
        is_live = id(e) in page_urls

        ccf_dls = ccf["deadlines"] if ccf else []
        hf_dls = hf["deadlines"] if hf else []
        conflict = False
        if ccf_dls and hf_dls:
            ccf_p = [d for d in ccf_dls if d["type"] == "paper"]
            hf_p = [d for d in hf_dls if d["type"] == "paper"]
            if is_live and ccf_p and hf_p and not any(same_time(a["when"], b["when"]) for a in ccf_p for b in hf_p):
                conflict = True
                ccf_ok = any(found_on(texts, d["raw"], d["when"]) for d in ccf_p)
                hf_ok = any(found_on(texts, d["raw"], d["when"]) for d in hf_p)
                chosen = hf_dls if (hf_ok and not ccf_ok) else ccf_dls
                chosen_src = "huggingface" if chosen is hf_dls else "ccfddl"
                log(f"  ⚠ {key}{year}: trackers disagree → using {chosen_src}"
                    f" (official page: ccf={ccf_ok} hf={hf_ok})")
            else:
                # agree (or only one has a paper deadline): take the richer list
                chosen = ccf_dls if len(ccf_dls) >= len(hf_dls) else hf_dls
                chosen_src = "ccfddl" if chosen is ccf_dls else "huggingface"
        else:
            chosen = ccf_dls or hf_dls
            chosen_src = "ccfddl" if ccf_dls else ("huggingface" if hf_dls else primary["source"])

        deadlines = []
        for d in sorted(chosen, key=lambda d: d["when"]):
            item = dict(type=d["type"], label=d["label"], utc=d["when"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
                        original=f'{d["raw"]} {d["tz"]}')
            if d["when"] >= horizon:
                if not verify:
                    item["status"] = "unchecked"
                elif not texts:
                    item["status"] = "unreachable"
                else:
                    item["status"] = "verified" if found_on(texts, d["raw"], d["when"]) else "unverified"
            deadlines.append(item)

        def pick(field):
            for r in (ccf, hf):
                if r and r.get(field):
                    return r[field]
            return None

        ranks = {}
        for r in (hf, ccf):
            if r:
                ranks.update(r.get("ranks") or {})
        result.append(dict(
            id=f"{key}{year}", key=key, title=pick("title"), year=year, full_name=pick("full_name"),
            subs=cat["subs"], link=(ccf or hf).get("link") or pick("link"), date=pick("date"), place=pick("place"),
            ranks=ranks, deadlines=deadlines, source=chosen_src, sources=sorted(src),
            conflict=conflict, note=(hf or {}).get("note")))
    return result


def apply_overrides(confs, catalog):
    path = ROOT / "overrides.yaml"
    if not path.exists():
        return confs
    data = yaml.safe_load(path.read_text()) or []
    idx = {c["id"]: c for c in confs}
    for o in data:
        key = norm(o["key"])
        cid = f"{key}{o['year']}"
        c = idx.get(cid)
        if c is None:
            cat = catalog.get(key, {"subs": o.get("subs", ["MISC"])})
            c = dict(id=cid, key=key, title=o.get("title", key.upper()), year=o["year"], full_name=None,
                     subs=o.get("subs", cat["subs"]), link=None, date=None, place=None, ranks={},
                     deadlines=[], source="override", sources=["override"], conflict=False, note=None)
            confs.append(c)
            idx[cid] = c
        for f in ("title", "full_name", "link", "date", "place", "note", "subs"):
            if f in o:
                c[f] = o[f]
        if "deadlines" in o:
            c["deadlines"] = []
            for d in o["deadlines"]:
                w = parse_when(d["date"], d.get("timezone"))
                c["deadlines"].append(dict(type=d.get("type", "paper"), label=d.get("label", "Paper submission"),
                                           utc=w.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                                           original=f'{d["date"]} {tz_label(d.get("timezone"))}', status="manual"))
            c["source"] = "override"
            c["sources"] = sorted(set(c["sources"]) | {"override"})
    return confs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-verify", action="store_true", help="skip official-site checks")
    ap.add_argument("--keep-years", type=int, default=4, help="editions older than this many years are dropped")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "catalog.yaml").read_text())
    catalog = {norm(str(k)): v for k, v in cfg["conferences"].items()}
    alias = {}
    for k, v in catalog.items():
        alias[k] = k
        for a in v.get("aliases") or []:
            alias[norm(a)] = k

    def resolve(title):
        return alias.get(norm(title or ""))

    records = []
    for loader in (load_ccf, load_hf):
        try:
            got = loader(resolve)
            log(f"  {loader.__name__}: {len(got)} editions")
            records += got
        except Exception as e:
            log(f"  ! {loader.__name__} failed: {e}")
    if not records:
        sys.exit("no data from any source; keeping previous data/deadlines.js")

    min_year = NOW.year - args.keep_years
    records = [r for r in records if r["year"] >= min_year]
    confs = apply_overrides(merge(records, catalog, verify=not args.no_verify), catalog)

    out = dict(generated=NOW.isoformat(timespec="seconds").replace("+00:00", "Z"),
               categories=cfg["categories"],
               sources={"ccfddl": "https://github.com/ccfddl/ccf-deadlines",
                        "huggingface": "https://github.com/huggingface/ai-deadlines"},
               conferences=confs)
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "deadlines.json").write_text(payload)
    (ROOT / "data" / "deadlines.js").write_text(f"window.DEADLINES = {payload};\n")

    now_s = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    upcoming = [c for c in confs if any(d["utc"] > now_s for d in c["deadlines"])]
    st = {}
    for c in upcoming:
        for d in c["deadlines"]:
            if "status" in d:
                st[d["status"]] = st.get(d["status"], 0) + 1
    log(f"✓ {len(confs)} editions, {len(upcoming)} with upcoming deadlines; check status: {st}")


if __name__ == "__main__":
    main()
