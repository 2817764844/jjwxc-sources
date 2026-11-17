# -*- coding: utf-8 -*-
"""女频源并行深挖（上一版串行，160 个域名磨了 20 分钟还没完）。

从 female_domain_pool.json 读域名池，快筛 + 女频书探针全部并行。

用法:
    python deep_female.py --workers 8
    python deep_female.py --skip-alive        # 跳过快筛，直接深挖全部
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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

FEMALE_BOOKS = [
    ("老婆粉了解一下[娱乐圈]", 3953941),
    ("三分野", 3583628),
    ("世上还有这种好事儿", 6576011),
]


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


def deep(site, probes, baseline, primary):
    """返回 (rec, 用哪本书)"""
    for kw in probes:
        base = baseline if kw == primary else None
        try:
            r = pn.probe(site, kw, base)
        except Exception as e:
            return None, f"{type(e).__name__}"
        if r.get("ok"):
            return r, kw
    return None, "女频书都搜不到"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--alive-workers", type=int, default=20)
    ap.add_argument("--pool", default="female_domain_pool.json")
    ap.add_argument("--skip-alive", action="store_true")
    ap.add_argument("--out", default="female_json_sources.json")
    args = ap.parse_args()

    pool = json.load(open(args.pool, encoding="utf-8"))
    doms = list(pool.keys())
    print(f"域名池 {len(doms)} 个")

    if args.skip_alive:
        sites = ["https://" + d for d in doms]
    else:
        print(f"\n① 快筛（并发 {args.alive_workers}）…")
        t0 = time.time()
        sites = []
        with ThreadPoolExecutor(max_workers=args.alive_workers) as ex:
            futs = {ex.submit(alive, d): d for d in doms}
            for f in as_completed(futs):
                s = f.result()
                if s:
                    sites.append(s)
        print(f"  可达 {len(sites)}/{len(doms)}  用时 {time.time()-t0:.0f}s")
        for s in sorted(sites):
            print(f"    {s}")

    if not sites:
        return

    print("\n② 抓女频基准…")
    baseline, primary = None, None
    for title, nid in FEMALE_BOOKS:
        txt, cid = vs.jjwxc_free_chapter(nid)
        if txt:
            baseline, primary = txt, title
            print(f"  ✓ 《{title}》第{cid}章 {len(vs.clean(txt))} 字")
            break
        print(f"  ✗ 《{title}》抓不到")
    probes = ([primary] + [t for t, _ in FEMALE_BOOKS if t != primary]) if primary else []

    print(f"\n③ 并行深挖 {len(sites)} 个站（并发 {args.workers}，主探针《{primary}》）…")
    t0 = time.time()
    hits, funcs, fails = [], [], []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(deep, s, probes, baseline, primary): s for s in sites}
        for f in as_completed(futs):
            site = futs[f]
            done += 1
            try:
                rec, used = f.result()
            except Exception as e:
                rec, used = None, type(e).__name__
            if rec:
                v = rec["verified"]
                m = v.get("match")
                tag = f" 内容{m:.2f}" if isinstance(m, float) else "  功能可用"
                print(f"  [{done}/{len(sites)}] ✅ {site:34s} 《{used}》{v['chapters']:>4}章{tag}")
                (hits if isinstance(m, float) else funcs).append(rec)
            else:
                fails.append((site, used))
    print(f"  用时 {time.time()-t0:.0f}s")

    print(f"\n{'='*74}")
    print(f"女频内容通过 {len(hits)} | 功能可用 {len(funcs)} | 失败 {len(fails)}")
    print("=" * 74)
    for r in hits + funcs:
        v = r["verified"]
        m = v.get("match")
        print(f"  {r['site']:36s} {v['chapters']:>4}章 《{v.get('kw')}》"
              + (f"  内容{m:.2f}" if isinstance(m, float) else ""))

    json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "baseline": primary,
               "ok": hits + funcs, "verified": hits, "func_only": funcs,
               "failed": [{"site": s, "why": w} for s, w in fails]},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细 -> {args.out}")


if __name__ == "__main__":
    main()
