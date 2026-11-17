# -*- coding: utf-8 -*-
"""女频书源专项搜索

思路和上一轮不同：
  上一轮是「拿一堆域名去试」，这批域名全是男频笔趣阁系，女频文本根本不在它们采集范围。
  这轮改从**已知能搜到晋江书的站**（96读书 / 格格党系）往外爬兄弟站，
  再用女频书（《老婆粉了解一下》等）当探针 —— 有这本书的站才是女频站。

用法:
    python hunt_female.py                    # 完整流程
    python hunt_female.py --seed-only        # 只看能爬出哪些域名
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import urllib3
from bs4 import BeautifulSoup

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

# 已知能搜到晋江书 / 女频定位的种子站
SEEDS = [
    "https://www.96dushu.com",       # 当前唯一可用的（格格党系）
    "https://www.lierchemical.com",  # 晚舟书苑，搜到过月下蝶影的书
    "https://www.5200xs.com",
    "https://www.biquge5200.cc",
    "https://www.uuks5.com",
    "https://www.xbiquge.la",
    "https://www.ddxs.com",
    "https://www.qbtr.cc",
    "https://www.69shu.top",
    "https://www.ibiquges.info",
]

# 女频探针书（晋江），第一本是主探针
FEMALE_BOOKS = [
    ("老婆粉了解一下[娱乐圈]", 3953941),
    ("三分野", 3583628),
    ("世上还有这种好事儿", 6576011),
    ("李世民为弟弟剧透头", 7572037),
]

SKIP_HOST = ("baidu", "qq.com", "weibo", "zhihu", "sohu", "163.com", "sina",
             "google", "bing", "cloudflare", "jsdelivr", "github", "w3.org",
             "miit.gov.cn", "beian", "12377", "mps.gov.cn", "csdn", "jianshu")


def harvest_links(seeds):
    """从种子站爬外链域名（导航/友情链接里的兄弟站）"""
    found = {}
    for site in seeds:
        try:
            r = http.get(site, headers=H, timeout=12, impersonate="chrome",
                         verify=False, allow_redirects=True)
            if r.status_code >= 400:
                print(f"  ✗ {site:36s} {r.status_code}")
                continue
            host = urlparse(r.url).netloc.lower()
            soup = BeautifulSoup(r.text, "lxml")
            hosts = set()
            for a in soup.find_all("a", href=True):
                h = urlparse(urljoin(r.url, a["href"])).netloc.lower()
                if not h or h == host:
                    continue
                if any(s in h for s in SKIP_HOST):
                    continue
                if not re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,6}$", h):
                    continue
                hosts.add(h)
            for h in hosts:
                found.setdefault(h, set()).add(host)
            print(f"  ✓ {site:36s} 外链 {len(hosts)} 个")
        except Exception as e:
            print(f"  ✗ {site:36s} {type(e).__name__}")
        time.sleep(0.5)

    # 从书页里再挖一层（书页常挂"本站推荐""同类推荐"）
    for dom in list(found.keys())[:20]:
        try:
            r = http.get("https://" + dom, headers=H, timeout=8,
                         impersonate="chrome", verify=False)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.find_all("a", href=True):
                h = urlparse(urljoin(r.url, a["href"])).netloc.lower()
                if h and h != dom and not any(s in h for s in SKIP_HOST):
                    if re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,6}$", h):
                        found.setdefault(h, set()).add(dom)
        except Exception:
            pass
    return found


def alive(dom, timeout=6):
    for scheme in ("https://", "http://"):
        try:
            r = http.get(scheme + dom, headers=H, timeout=timeout,
                         impersonate="chrome", verify=False, allow_redirects=True)
            if r.status_code < 400 and len(r.text or "") > 800:
                if "小说" in r.text or "章节" in r.text or "阅读" in r.text:
                    soup = BeautifulSoup(r.text, "lxml")
                    t = (soup.title.get_text(strip=True) if soup.title else "")[:26]
                    return {"site": scheme + dom, "title": t}
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out", default="female_sources.json")
    args = ap.parse_args()

    print("① 从种子站爬兄弟站域名…")
    links = harvest_links(SEEDS)
    doms = sorted(links.keys())
    print(f"\n  爬到 {len(doms)} 个候选域名")
    for d in doms[:40]:
        print(f"    {d:32s} ← {','.join(sorted(links[d]))[:40]}")
    if args.seed_only:
        return

    print(f"\n② 快筛可达性（并发 {args.workers}）…")
    ok_sites = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(alive, d): d for d in doms}
        for f in as_completed(futs):
            r = f.result()
            if r:
                ok_sites.append(r)
                print(f"  ✓ {r['site']:34s} {r['title']}")
    print(f"\n  可达 {len(ok_sites)}/{len(doms)}")
    if not ok_sites:
        print("  没有可达站点")
        return

    print("\n③ 抓女频基准（晋江免费章）…")
    baseline, book_title, novelid = None, None, None
    for title, nid in FEMALE_BOOKS:
        txt, cid = vs.jjwxc_free_chapter(nid)
        if txt:
            baseline, book_title, novelid = txt, title, nid
            print(f"  ✓ 《{title}》第{cid}章，{len(vs.clean(txt))} 字")
            print(f"    开头：{txt[:50]!r}")
            break
        print(f"  ✗ 《{title}》抓不到免费章")
    if not baseline:
        print("  拿不到女频基准，退回纯功能验证")

    probes = [book_title] + [t for t, _ in FEMALE_BOOKS if t != book_title] if book_title else []

    print(f"\n④ 用女频书深挖 {len(ok_sites)} 个站…")
    hits, funcs = [], []
    for i, item in enumerate(ok_sites, 1):
        site = item["site"]
        rec = None
        for kw in probes:
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

    print(f"\n{'='*70}")
    print(f"女频内容校验通过 {len(hits)} | 功能可用 {len(funcs)}")
    print("=" * 70)
    for r in hits + funcs:
        v = r["verified"]
        m = v.get("match")
        print(f"  {r['site']:34s} {v['chapters']:>4}章 《{v.get('kw')}》"
              + (f"  内容{m:.2f}" if isinstance(m, float) else ""))

    json.dump({"when": time.strftime("%Y-%m-%d %H:%M"),
               "baseline_book": book_title, "ok": hits + funcs,
               "reached": ok_sites},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细 -> {args.out}")


if __name__ == "__main__":
    main()
