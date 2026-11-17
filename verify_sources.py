# -*- coding: utf-8 -*-
"""书源内容校验 —— 判断某个源下到的到底是不是这本书。

原理：
  镜像站的正文都是从晋江扒下来的，所以拿**晋江官方免费章**当标准答案，
  和每个源的第 1 章做相似度比对，就能识别「搜索命中但内容是别的书」的站。

  晋江全书 VIP 没有免费章时，退化为「多源多数投票」：
  所有源的第 1 章两两比对，跟大家都不像的那个是假的。

相似度用 **2-gram Dice 系数**（不是逐字符按位置比）：
  镜像站正文常有广告前缀、错字、段落合并，按位置比会误判成 0；
  n-gram 集合比对对这些干扰不敏感。

用法:
    python verify_sources.py --novelid 3433838              # 校验 sources.json 里所有启用的源
    python verify_sources.py --novelid 3433838 --apply       # 同时把结果写回 sources.json（不合格自动禁用）
    python verify_sources.py --kw 勿扰飞升 --no-jjwxc        # 不用晋江基准，走多源投票
"""
import argparse
import json
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import booksource as bs
import mirror_scraper as ms

THRESHOLD = 0.55          # 低于这个判为「不是同一本书」
DROP_LIST = [             # 镜像站常见的垃圾前后缀
    r"最新网址[：:].{0,40}", r"请记住本站.{0,20}", r"一秒记住.{0,20}",
    r"手机版阅读网址.{0,30}", r"本站域名.{0,30}", r"无弹窗.{0,10}",
    r"最快更新.{0,20}", r"章节错误.{0,30}", r"\d{2,4}读书网.{0,20}",
]


# ---------------------------------------------------------------------------
def clean(t):
    t = t or ""
    for p in DROP_LIST:
        t = re.sub(p, "", t)
    # 只留汉字，去掉所有空白、标点、英文数字
    return re.sub(r"[^\u4e00-\u9fff]", "", t)


def ngram_sim(a, b, n=2):
    """2-gram 相似度，取 Dice 与「包含度」的较大值。

    为什么要两个指标：
      · Dice = 2|A∩B|/(|A|+|B|)，两队长度差太多会被严重压低 ——
        镜像站常把一章拆成分页（只给第一页几百字），Dice 只有 0.34，
        但那段内容其实完全出自同一章。
      · 包含度 = |A∩B| / min(|A|,|B|)，短的一方被长的一方覆盖就算高，
        正好对上「分页/节选」的情况。

    短文本（<150 汉字）容易靠常见短语蒙混，这种只认 Dice。
    """
    A, B = clean(a), clean(b)
    if len(A) < n or len(B) < n:
        return 0.0
    ga = {A[i:i + n] for i in range(len(A) - n + 1)}
    gb = {B[i:i + n] for i in range(len(B) - n + 1)}
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    dice = 2 * inter / (len(ga) + len(gb))
    if min(len(A), len(B)) < 150:
        return round(dice, 4)
    contain = inter / min(len(ga), len(gb))
    return round(max(dice, contain), 4)


# ---------------------------------------------------------------------------
# 晋江正文前面会混进这些界面文字，去掉再当基准
UI_NOISE = ("添加书签", "推荐给朋友", "投诉", "文章收藏", "收藏类别", "为收藏文章分类",
            "新增收藏", "字号", "背景", "返回顶部", "下一章", "上一章", "目录",
            "添加書籤", "投推荐票", "营养液", "地雷", "手榴弹", "火箭炮", "深水鱼雷")


def strip_ui_noise(txt):
    keep = []
    for line in (txt or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        if any(k in s for k in UI_NOISE) and len(s) < 30:
            continue
        keep.append(s)
    # 第一个长行（>=40 字）才是正文开头，前面的都是导航
    for i, s in enumerate(keep):
        if len(s) >= 40:
            return "\n".join(keep[i:])
    return "\n".join(keep)


def jjwxc_free_chapter(novelid, max_try=6):
    """从晋江抓免费章正文当标准答案。返回 (正文, 章节号) 或 (None, None)。

    晋江免费章是明文（div.noveltext），不需要登录；VIP 章会跳登录页，跳过。
    注意：晋江页面是 GB18030，curl_cffi 有时会把 encoding 猜成 utf-8，
    所以这里直接用原始字节按 gb18030 解，不能信 r.text。
    """
    if not novelid:
        return None, None
    from bs4 import BeautifulSoup
    for cid in range(1, max_try + 1):
        url = f"https://www.jjwxc.net/onebook.php?novelid={novelid}&chapterid={cid}"
        r = ms.fetch(url, referer="https://www.jjwxc.net/")
        if r is None:
            continue
        if "login.php" in (r.url or ""):
            continue
        html = r.content.decode("gb18030", "ignore") if r.content else (r.text or "")
        if "noveltext" not in html:
            continue
        soup = BeautifulSoup(html, "lxml")
        node = soup.select_one("div.noveltext") or soup.select_one("div.novelbody")
        if not node:
            continue
        # 注意：正文本身是包在 <div> 里的，decompose 掉 div 会把字一起删掉
        for j in node.select("script,style,noscript"):
            j.decompose()
        for br in node.find_all(["br", "p", "div"]):
            br.insert_after("\n")
        txt = strip_ui_noise(node.get_text("\n", strip=True))
        if len(clean(txt)) >= 200:
            return txt, cid
    return None, None


def fetch_chapter1(rec, kw):
    """在一个源上：搜书 → 目录 → 真正的第 1 章正文"""
    hit = ms.search_one(rec, kw, [kw], "")
    if not hit:
        return None, "搜索没命中"
    toc_sel, links, toc_url, _ = ms.get_toc(rec, hit["url"])
    if not toc_sel:
        return None, "目录没解析出来"
    toc = ms.build_toc(toc_url, links)
    if not toc:
        return None, "目录为空"
    # 有的站目录倒序，取章号最小的那章
    for ch in toc[:3]:
        csel, txt = ms.get_content(rec, ch["url"], toc_url)
        if txt and ms.han(txt) >= 100:
            return txt, f"{hit['title']} / {ch['title']}"
        time.sleep(0.5)
    return None, "正文抓不出来"


def majority_truth(texts):
    """多源投票：跟其他源平均相似度最高的那个当基准"""
    if not texts:
        return None
    best, best_score = None, -1
    for i, t in enumerate(texts):
        s = sum(ngram_sim(t, o) for j, o in enumerate(texts) if j != i)
        s = s / max(len(texts) - 1, 1)
        if s > best_score:
            best, best_score = t, s
    return best


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default="勿扰飞升")
    ap.add_argument("--novelid", type=int, default=0, help="晋江 novelid，用来抓免费章当基准")
    ap.add_argument("--no-jjwxc", action="store_true", help="不用晋江基准，走多源投票")
    ap.add_argument("--apply", action="store_true", help="把结果写回 sources.json，不合格的禁用")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--only", default="", help="只测名字里含这个字符串的源")
    ap.add_argument("--path", default=None)
    args = ap.parse_args()

    srcs = [s for s in bs.load_sources(args.path) if s.get("enabled", True)]
    if args.only:
        srcs = [s for s in srcs if args.only in s["name"] or args.only in s["site"]
                or args.only in " ".join(s.get("tags") or [])]
    if not srcs:
        print("没有要校验的源")
        return

    print(f"关键词 = {args.kw}   待校验 {len(srcs)} 个源\n")

    # ---- 基准 ----
    truth, desc = None, None
    if not args.no_jjwxc and args.novelid:
        print(f"抓晋江免费章当基准（novelid={args.novelid}）…")
        truth, cid = jjwxc_free_chapter(args.novelid)
        if truth:
            desc = f"晋江第{cid}章"
            print(f"  ✓ {desc}：{len(clean(truth))} 个汉字")
            print(f"    开头：{truth[:60]!r}\n")
        else:
            print("  ✗ 没抓到免费章（可能全书 VIP），改用多源投票\n")

    # ---- 逐源抓第 1 章 ----
    results = []
    for rec in srcs:
        t0 = time.time()
        txt, note = fetch_chapter1(rec, args.kw)
        el = round(time.time() - t0, 1)
        if not txt:
            print(f"⚠️  {rec['name']:<12} {el:>5}s  {note}")
            results.append({"name": rec["name"], "site": rec["site"], "ok": False,
                            "reason": note})
            continue
        results.append({"name": rec["name"], "site": rec["site"], "ok": True,
                        "text": txt, "note": note, "cost": el})

    got = [r for r in results if r["ok"]]
    if not truth and got:
        truth = majority_truth([r["text"] for r in got])
        desc = "多源投票"
        print(f"\n多源投票基准：{len(got)} 个样本里最主流的那份\n")

    # ---- 打分 ----
    print(f"{'源':<12}{'相似度':>8}  {'判定':<8} 说明")
    print("-" * 78)
    for r in results:
        if not r["ok"]:
            print(f"{r['name']:<12}{'-':>8}  {'跳过':<8} {r['reason']}")
            r["match"] = None
            r["verdict"] = "skip"
            continue
        if truth:
            m = ngram_sim(r["text"], truth)
            ok = m >= args.threshold
            verdict = "✓ 真" if ok else "✗ 假"
        else:
            m, ok, verdict = None, True, "✓ 未校验"
        r["match"] = m
        r["verdict"] = "ok" if ok else "bad"
        print(f"{r['name']:<12}{(f'{m:.3f}' if m is not None else '-'):>8}  {verdict:<8} {r['note']}")
        if m is not None and not ok:
            print(f"{'':<12}{'':>8}  {'':<8} 抓到的是：{r['text'][:50]!r}")

    good = [r for r in results if r.get("verdict") == "ok"]
    bad = [r for r in results if r.get("verdict") == "bad"]
    skip = [r for r in results if r.get("verdict") == "skip"]
    print(f"\n通过 {len(good)} | 内容不符 {len(bad)} | 抓不到 {len(skip)}   基准={desc}")

    report = {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "kw": args.kw, "novelid": args.novelid, "baseline": desc,
        "threshold": args.threshold,
        "results": [{k: v for k, v in r.items() if k != "text"} for r in results],
    }
    with open("verify_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("报告 -> verify_report.json")

    # ---- 写回配置 ----
    if args.apply:
        doc = bs.load_doc(args.path)
        changed = 0
        for s in doc["sources"]:
            nm = bs.normalize(s)["name"]
            hit = next((r for r in results if r["name"] == nm), None)
            if not hit:
                continue
            if hit.get("match") is not None:
                s.setdefault("verified", {})
                s["verified"].update({"kw": args.kw, "match": hit["match"],
                                      "baseline": desc,
                                      "when": time.strftime("%Y-%m-%d")})
            if hit["verdict"] == "bad":
                s["enabled"] = False
                s["note"] = (f"内容校验不通过：与基准相似度 {hit['match']:.2f}"
                             f"（{desc}），抓到的是别的书")
                changed += 1
            elif hit["verdict"] == "ok":
                s.pop("note", None) if "内容校验不通过" in (s.get("note") or "") else None
                changed += 1
        doc["updated"] = time.strftime("%Y-%m-%d %H:%M")
        bs._write_doc(doc, args.path)
        print(f"已写回 sources.json，更新 {changed} 条（不合格的已禁用）")


if __name__ == "__main__":
    main()



