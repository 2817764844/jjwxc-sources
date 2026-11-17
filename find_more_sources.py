# -*- coding: utf-8 -*-
"""批量探测候选域名（带内容校验）—— 从书源仓库挖出来的域名走这一套。

用法:
    python find_more_sources.py                 # 探测 candidate_domains.txt
    python find_more_sources.py --limit 30      # 只测前 30 个
    python find_more_sources.py --kw 斗罗大陆    # 换关键词（换书验证更准）

输出: found_sources.json（只含校验通过的）
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import probe_new as pn

# 明显不是小说站的，跳过
SKIP_PATTERNS = [
    "baidu", "qq.com", "alipay", "gitlink", "github", "jsdelivr", "w3.org",
    "zhuishushenqi", "szjinruixin", "miyushu.com", "hdyp.net", "hxspp.com",
    "timgsa", "hm.baidu", "gnr.wodeapi", "appapi", "gaoduan", "res.wx",
]


def load_domains(path="candidate_domains.txt"):
    out = []
    try:
        for line in open(path, encoding="utf-8"):
            d = line.strip().lower()
            if not d or "#" in d:
                continue
            if any(p in d for p in SKIP_PATTERNS):
                continue
            if not re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,6}$", d):
                continue
            out.append(d)
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(out))


def probe_domain(dom, kw, baseline):
    for scheme in ("https://", "http://"):
        site = scheme + dom
        try:
            rec = pn.probe(site, kw, baseline)
            if rec.get("ok"):
                return rec
            # 首页就打不开的，http 大概率也不行，别浪费时间
            if "首页不可达" in str(rec.get("why")):
                continue
            return rec
        except Exception as e:
            last = {"site": site, "ok": False, "why": f"{type(e).__name__}"}
    return last if "last" in dir() else {"site": site, "ok": False, "why": "unreachable"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default="勿扰飞升")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", default="found_sources.json")
    ap.add_argument("--domains", default="candidate_domains.txt")
    args = ap.parse_args()

    doms = load_domains(args.domains)
    if args.limit:
        doms = doms[: args.limit]
    if not doms:
        print("候选列表为空，先把域名写进 candidate_domains.txt")
        return

    print(f"关键词 = {args.kw}")
    baseline, desc = pn.get_baseline("96读书", args.kw)
    if baseline:
        print(f"内容基准 = {desc}，{pn.han(baseline)} 字")
        print(f"  前 50 字: {baseline[:50]!r}")
    else:
        print("⚠ 没有内容基准，只能验证「能搜到 + 能解析」，无法识别蹭标题站")
    print(f"\n探测 {len(doms)} 个域名，并发 {args.workers}\n")

    t0 = time.time()
    ok, suspect, dead = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_domain, d, args.kw, baseline): d for d in doms}
        for f in as_completed(futs):
            try:
                rec = f.result()
            except Exception as e:
                rec = {"site": futs[f], "ok": False, "why": type(e).__name__}
            if rec.get("ok"):
                v = rec["verified"]
                m = v.get("match")
                ok.append(rec)
                mt = f" 内容{m:.2f}" if isinstance(m, float) else ""
                print(f"✅ {rec['site']:36s} toc={rec['toc_sel']:<16} "
                      f"正文={rec['content_sel']:<14} {v['chapters']:>4}章 "
                      f"{v['sample_han']:>5}字{mt}")
            elif rec.get("suspect"):
                suspect.append(rec)
                print(f"⚠️  {rec['site']:36s} {rec['why']}")
            else:
                dead.append(rec)

    print(f"\n{'='*74}")
    print(f"通过 {len(ok)} | 蹭标题 {len(suspect)} | 不可用 {len(dead)} | 用时 {time.time()-t0:.0f}s")
    print("=" * 74)
    ok.sort(key=lambda x: -(x["verified"].get("match") or 0))
    for r in ok:
        m = r["verified"].get("match")
        print(f"  {r['site']:36s} {r['verified']['chapters']:>4}章"
              + (f"  相似度{m:.2f}" if isinstance(m, float) else ""))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "kw": args.kw,
                   "ok": ok, "suspect": suspect,
                   "dead": [d.get("site") for d in dead]}, f, ensure_ascii=False, indent=1)
    print(f"\n明细 -> {args.out}")


if __name__ == "__main__":
    main()
