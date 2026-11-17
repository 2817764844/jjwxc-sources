# -*- coding: utf-8 -*-
"""找新书源：域名池 -> 可达性快筛 -> 深挖（搜索/目录/正文/内容校验）

上一轮直接对 60 个域名跑完整探测，807 秒全军覆没。
问题多半不在站，在于探测太重、失败点太靠后。这轮拆成三段：

  1) 域名池：多个公开书源仓库 + 内置候选
  2) 快筛：并发 HEAD/GET 首页，5 秒超时，只留真能连上的
  3) 深挖：只对能连上的跑搜索/目录/正文，并用晋江免费章做内容校验

用法:
    python hunt_sources.py --kw 勿扰飞升 --novelid 3433838
    python hunt_sources.py --fast-only          # 只跑快筛，看有多少站活着
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from curl_cffi import requests as http   # noqa: E402

import probe_new as pn                   # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

# 公开书源仓库（jsDelivr 托管，国内一般可直连）
REPO_JSONS = [
    "https://code.gitlink.org.cn/api/v1/repos/yi-c/yd/raw/sy.json",
    "https://cdn.jsdelivr.net/gh/yeyulingfeng01/yuedu.github.io@1.1/202003-1.txt",
    "https://cdn.jsdelivr.net/gh/laoyerror/laoyerror.github.io/ameng.json3",
    "https://cdn.jsdelivr.net/gh/mgz0227/yuedu@master/shuyuan.json",
    "https://cdn.jsdelivr.net/gh/Downagerd/gedoor@master/booksource.json",
    "https://cdn.jsdelivr.net/gh/shidahuilang/shuyuan@shuyuan/README.md",
]

SKIP = ("baidu", "qq.com", "alipay", "gitlink", "github", "jsdelivr", "w3.org",
        "zhuishushenqi", "szjinruixin", "miyushu", "hdyp.net", "hxspp", "timgsa",
        "wodeapi", "appapi", "gaoduan", "res.wx", "gstatic", "googleapis",
        "cloudflare", "jquery", "bootstrap", "cnzz", "51.la")

EXTRA = [
    # 上一轮 web_search 里明确出现、但还没测过的
    "cnsyhz.com", "biquge.cc", "shoujixs.net", "zym888.com", "ftmhidp.com",
    "ybwzx9e.com", "hahawx.com", "bqgkanshu.com", "wucuoxs.com", "xbanzhu.com",
    "biquge5200.net", "bqg221.cc", "bqg225.cc", "ibiquge.la", "biquges.info",
    "qieziwx.com", "zhuaijishuwu.com", "mingyuezw.com", "yiduxs.com", "shuhuixs.com",
    "69shu.top", "bqgui.vip", "beqege.net", "biqu.la", "23qb.net", "bqg128.com",
    "biquge99.net", "biqukan8.com", "bqg221.net", "shu99.net", "biqubook.com",
    "youshuwang.com", "duskshu.com", "xswang.com", "wanben.org", "quanben.io",
    "biquge.info", "biquge.tv", "biquge7.xyz", "bqg.la", "biquge5200.com",
    "diyibanzhu.net", "banzhu.org", "biquge520.cc", "shu8.cc", "bqg88.com",
    "bqg999.com", "bookben.com", "qishuta.com", "biquge.la", "bqg168.com",
]


def load_domain_pool(verbose=True):
    pool = set()
    for u in REPO_JSONS:
        try:
            r = http.get(u, headers=H, timeout=20, impersonate="chrome", verify=False)
            body = r.text or ""
            before = len(pool)
            for m in re.finditer(r'https?://([a-z0-9][a-z0-9.\-]{3,40}\.(?:com|net|org|cc|info|la|me|co|tv|xyz|vip|top|io|cn|sbs|club))',
                                 body, re.I):
                d = m.group(1).lower()
                if not any(s in d for s in SKIP):
                    pool.add(d)
            if verbose:
                print(f"  {r.status_code} {len(body):>8,}B  +{len(pool)-before:>3} 域名  {u[:60]}")
        except Exception as e:
            if verbose:
                print(f"  FAIL {type(e).__name__:<16} {u[:60]}")
    for d in EXTRA:
        pool.add(d)
    # 去掉明显不是小说站的
    out = []
    for d in sorted(pool):
        if len(d) > 40 or d.count(".") > 3:
            continue
        if not re.match(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,6}$", d):
            continue
        out.append(d)
    return out


def alive_probe(dom, timeout=6):
    """快筛：能连上、返回 HTML、不是错误页"""
    for scheme in ("https://", "http://"):
        site = scheme + dom
        t0 = time.time()
        try:
            r = http.get(site, headers=H, timeout=timeout,
                         impersonate="chrome", verify=False, allow_redirects=True)
            el = time.time() - t0
            if r.status_code >= 400:
                continue
            body = r.text or ""
            if len(body) < 800:
                continue
            low = body[:3000]
            if "Just a moment" in low or "cf-challenge" in low:
                return {"site": site, "status": "cf", "ms": el}
            soup = BeautifulSoup(body, "lxml")
            title = (soup.title.get_text(strip=True) if soup.title else "")[:24]
            has_novel = any(k in body for k in ("小说", "章节", "阅读", "最新章节"))
            if not has_novel:
                continue
            return {"site": site, "status": "ok", "ms": el, "title": title, "len": len(body)}
        except Exception:
            continue
    return {"site": "https://" + dom, "status": "dead", "ms": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default="勿扰飞升")
    ap.add_argument("--novelid", type=int, default=3433838)
    ap.add_argument("--fast-only", action="store_true")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--out", default="hunted_sources.json")
    args = ap.parse_args()

    print("① 收集域名池…")
    doms = load_domain_pool()
    print(f"  合计 {len(doms)} 个域名\n")

    print(f"② 可达性快筛（并发 {args.workers}，{len(doms)} 个）…")
    t0 = time.time()
    alive, cf, dead = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(alive_probe, d): d for d in doms}
        for f in as_completed(futs):
            r = f.result()
            if r["status"] == "ok":
                alive.append(r)
                print(f"  ✓ {r['site']:36s} {r['ms']:.1f}s  {r.get('title','')}")
            elif r["status"] == "cf":
                cf.append(r)
            else:
                dead.append(r)
    print(f"\n  可达 {len(alive)} | Cloudflare {len(cf)} | 死 {len(dead)} | 用时 {time.time()-t0:.0f}s")

    json.dump({"alive": alive, "cf": cf, "dead": [d["site"] for d in dead]},
              open("hunt_scan.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  扫描明细 -> hunt_scan.json")

    if args.fast_only:
        return

    if not alive:
        print("\n没有可达站点，没法深挖。")
        return

    print(f"\n③ 深挖 {len(alive)} 个可达站（搜索 + 目录 + 正文 + 内容校验）…")
    baseline, desc = pn.get_baseline("96读书", args.kw)
    if baseline:
        print(f"  内容基准 = {desc}，{pn.han(baseline)} 字")
    else:
        print("  ⚠ 没拿到内容基准，无法识别蹭标题站")
    # 备用探针书：笔趣阁系基本都有，用来验证「站本身能不能用」
    FB = ["斗罗大陆", "诡秘之主", "完美世界"]
    print(f"  主书搜不到时，改用备用书验证功能：{FB}\n")

    ok, func_only = [], []
    for i, item in enumerate(alive, 1):
        site = item["site"]
        rec, used_kw = None, None

        # 先用目标书（能做内容校验）
        try:
            r1 = pn.probe(site, args.kw, baseline)
        except Exception as e:
            r1 = {"site": site, "ok": False, "why": type(e).__name__}
        if r1.get("ok"):
            rec, used_kw = r1, args.kw

        # 搜不到就换备用书，只验证功能（无内容基准，match=None）
        if rec is None and "搜索没打通" in str(r1.get("why")):
            for fk in FB:
                try:
                    r2 = pn.probe(site, fk, None)
                except Exception:
                    continue
                if r2.get("ok"):
                    r2["verified"]["match"] = None
                    r2["verified"]["note"] = f"用《{fk}》验证功能正常（未做内容校验）"
                    rec, used_kw = r2, fk
                    break

        if rec:
            v = rec["verified"]
            m = v.get("match")
            tag = f" 内容{m:.2f}" if isinstance(m, float) else "  功能可用"
            print(f"  [{i}/{len(alive)}] ✅ {site:32s} 《{used_kw}》 {v['chapters']:>4}章{tag}")
            (ok if isinstance(m, float) else func_only).append(rec)
        else:
            print(f"  [{i}/{len(alive)}]    {site:32s} {r1.get('why','')}")

    ok.sort(key=lambda x: -(x["verified"].get("match") or 0))
    all_ok = ok + func_only
    print(f"\n{'='*70}")
    print(f"内容校验通过 {len(ok)} 个 | 功能可用（未校验）{len(func_only)} 个 | 合计 {len(all_ok)}")
    print("=" * 70)
    for r in all_ok:
        m = r["verified"].get("match")
        kw_used = r["verified"].get("kw")
        print(f"  {r['site']:34s} {r['verified']['chapters']:>4}章"
              + (f"  内容{m:.2f}" if isinstance(m, float) else "  功能可用")
              + f"  《{kw_used}》")

    json.dump({"when": time.strftime("%Y-%m-%d %H:%M"), "kw": args.kw,
               "ok": all_ok, "verified": ok, "func_only": func_only,
               "alive": alive}, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n明细 -> {args.out}")


if __name__ == "__main__":
    main()
