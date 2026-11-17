# -*- coding: utf-8 -*-
"""从 legado 女频书源 JSON 里提取站点，再用女频书探针验证。

数据源（社区维护的女频源池）：
  女频耽美向 64 个 / 女频 60 个 / 女生耽美 49 个 / 耽美BL
这些源是有人按「女频」分好组的，域名池质量比盲猜高得多。

用法:
    python harvest_female_json.py --dump          # 只提取域名，看有多少
    python harvest_female_json.py                 # 提取 + 快筛 + 女频书深挖
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import urllib3

urllib3.disable_warnings()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from curl_cffi import requests as http   # noqa: E402

import probe_new as pn                   # noqa: E402
import verify_sources as vs              # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

FEEDS = [
    ("女频耽美64", "https://gcore.jsdelivr.net/gh/yuedu520/yuedu/2503111.json"),
    ("女频60",     "https://gcore.jsdelivr.net/gh/yuedu520/yuedu/2504171.json"),
    ("女生耽美49", "https://fastly.jsdelivr.net/gh/yuedu520/yuedu/250330.json"),
    ("耽美BL",     "https://testingcf.jsdelivr.net/gh/yuedu520/yuedu/250417.json"),
    ("通用合集",   "https://gcore.jsdelivr.net/gh/yuedu520/yuedu/2503141.json"),
]

FEMALE_BOOKS = [
    ("老婆粉了解一下[娱乐圈]", 3953941),
    ("三分野", 3583628),
    ("世上还有这种好事儿", 6576011),
]

SKIP_HOST = ("baidu", "qq.com", "weibo", "zhihu", "sohu", "163.com", "sina",
             "google", "bing", "cloudflare", "jsdelivr", "github", "w3.org",
             "miit", "beian", "12377", "mps.gov", "qidian", "jjwxc", "hongxiu",
             "zongheng", "17k", "ciweimao", "faloo", "tomato", "fanqie")


def fetch_feed(url):
    for imp in ("chrome", "chrome120"):
        try:
            r = http.get(url, headers=H, timeout=25, impersonate=imp, verify=False)
            if r.status_code != 200:
                continue
            txt = r.text.strip()
            if not txt.startswith(("[", "{")):
                continue
            data = json.loads(txt)
            if isinstance(data, dict):
                data = data.get("bookSources") or data.get("sources") or [data]
            return data if isinstance(data, list) else []
        except Exception:
            continue
    return []


def extract(verbose=True):
    """返回 {域名: {names:set, groups:set, urls:set}}"""
    pool = {}
    for label, url in FEEDS:
        data = fetch_feed(url)
        if not data:
            if verbose:
                print(f"  ✗ {label:10s} 拉取失败  {url[:64]}")
            continue
        n0 = len(pool)
        for s in data:
            if not isinstance(s, dict):
                continue
            u = s.get("bookSourceUrl") or s.get("url") or ""
            if not u.startswith("http"):
                continue
            host = urlparse(u).netloc.lower()
            if not host or any(k in host for k in SKIP_HOST):
                continue
            if not re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,6}$", host):
                continue
            rec = pool.setdefault(host, {"names": set(), "groups": set(), "urls": set()})
            nm = (s.get("bookSourceName") or "").strip()
            if nm:
                rec["names"].add(nm[:20])
            gr = (s.get("bookSourceGroup") or "").strip()
            if gr:
                rec["groups"].update(x.strip() for x in re.split(r"[,，]", gr) if x.strip())
            rec["urls"].add(u.rstrip("/"))
        if verbose:
            print(f"  ✓ {label:10s} {len(data):>4} 条源  +{len(pool)-n0:>3} 新域名")
    return pool


def alive(dom, timeout=6):
    for scheme in ("https://", "http://"):
        try:
            r = http.get(scheme + dom, headers=H, timeout=timeout,
                         impersonate="chrome", verify=False, allow_redirects=True)
            if r.status_code < 400 and len(r.text or "") > 800:
                if any(k in r.text for k in ("小说", "章节", "阅读", "book")):
                    return scheme + dom
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out", default="female_json_sources.json")
    args = ap.parse_args()

    print("① 拉女频书源 JSON 并提取域名…")
    pool = extract()
    print(f"\n  合计 {len(pool)} 个唯一域名")

    # 按分组排序，女频相关优先
    def rank(d):
        g = " ".join(pool[d]["groups"])
        return (0 if re.search(r"女频|言情|耽美|女生|BL|bl", g) else 1, d)

    doms = sorted(pool, key=rank)
    for d in doms[:50]:
        info = pool[d]
        g = ",".join(sorted(info["groups"]))[:20]
        n = ",".join(sorted(info["names"]))[:24]
        print(f"    {d:32s} [{g:20s}] {n}")

    json.dump({d: {"names": sorted(v["names"]), "groups": sorted(v["groups"])}
               for d, v in pool.items()},
              open("female_domain_pool.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if args.dump:
        print("\n（--dump：只提取，不探测）")
        return

    print(f"\n② 快筛可达性（并发 {args.workers}，{len(doms)} 个）…")
    t0 = time.time()
    ok_sites = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(alive, d): d for d in doms}
        for f in as_completed(futs):
            s = f.result()
            if s:
                ok_sites.append(s)
                print(f"  ✓ {s}")
    print(f"\n  可达 {len(ok_sites)}/{len(doms)}  用时 {time.time()-t0:.0f}s")
    if not ok_sites:
        return

    print("\n③ 抓女频基准…")
    baseline, book_title = None, None
    for title, nid in FEMALE_BOOKS:
        txt, cid = vs.jjwxc_free_chapter(nid)
        if txt:
            baseline, book_title = txt, title
            print(f"  ✓ 《{title}》第{cid}章 {len(vs.clean(txt))} 字")
            break
        print(f"  ✗ 《{title}》抓不到免费章")
    probes = [t for t, _ in FEMALE_BOOKS if t != book_title] if book_title else []

    print(f"\n④ 用女频书深挖 {len(ok_sites)} 个站（主探针《{book_title}》）…")
    hits, funcs = [], []
    for i, site in enumerate(ok_sites, 1):
        rec, used = None, None
        for kw in ([book_title] + probes):
            base = baseline if kw == book_title else None
            try:
                r = pn.probe(site, kw, base)
            except Exception:
                continue
            if r.get("ok"):
                rec, used = r, kw
                break
        if rec:
            v = rec["verified"]
            m = v.get("match")
            tag = f" 内容{m:.2f}" if isinstance(m, float) else "  功能可用"
            print(f"  [{i}/{len(ok_sites)}] ✅ {site:32s} 《{used}》{v['chapters']:>4}章{tag}")
            (hits if isinstance(m, float) else funcs).append(rec)
        else:
            print(f"  [{i}/{len(ok_sites)}]    {site:32s} 女频书都搜不到")

    print(f"\n{'='*72}")
    print(f"女频内容通过 {len(hits)} | 功能可用 {len(funcs)} | 合计 {len(hits)+len(funcs)}")
    print("=" * 72)
    for r in hits + funcs:
        v = r["verified"]
        m = v.get("match")
        print(f"  {r['site']:36s} {v['chapters']:>4}章 《{v.get('kw')}》"
              + (f"  内容{m:.2f}" if isinstance(m, float) else "  功能可用"))

    json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "baseline": book_title,
               "ok": hits + funcs, "verified": hits, "func_only": funcs},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细 -> {args.out}")


if __name__ == "__main__":
    main()
