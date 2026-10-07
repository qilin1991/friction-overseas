#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google News 跳转链解码 + 正文回补。**必须在 GitHub Actions 的 Azure IP 上跑。**

为什么不能在 VPS 上跑（2026-10-07 实测）：
  · VPS 连不上 news.google.com（curl code=000，被墙）；
  · counterman.com / import-car.com 这类发布站对 VPS 的机房 IP 直接 403
    （Cloudflare，返回 5588 字节拦截页）；
  · 2024 年后 Google News 的 /rss/articles/ 链接是不透明的 AU_yqL 串，
    **无法离线 base64 解码**，只能调 batchexecute 接口。
同一脚本在 Actions（出口 4.246.62.229）实测：news.google.com 200、
counterman 200、import-car 200，解码 5/5 成功、正文 3/5 抓成。

流程（每条独立，一条失败不影响其余）：
  ① GET 壳页，正则取 data-n-a-sg / data-n-a-ts
  ② POST batchexecute（Fbv4je / garturlreq）解出真实文章 URL
  ③ GET 真实 URL，trafilatura 抽正文
  ④ **逐条 append** 到 JSONL —— 不是最后一次性写。上一版补抓把结果攒到最后，
     挂死一次 138 条全白干（教训见 vps-nginx-subsite skill 坑 15）。

用法：
  python scripts/gnews_decode_fetch.py \
      --pending data/pending_google.json \
      --out data/gnews_content.jsonl \
      --conc 8 --budget-min 60 --limit 0

可重跑：已在 --out 里的 article_id 会跳过，直接续上。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 抓到的正文短于此值不算成功（trafilatura 对拦截页也会吐几十个字）
MIN_TEXT = 400

# 拦截/验证页特征。判的是**抓到的正文**，不是原始 HTML ——
# 正常页面 <noscript> 里就带 "enable JavaScript"，扫 HTML 会误杀。
WALL_MARK = (
    "enable javascript and cookies to continue",
    "checking your browser",
    "just a moment",
    "attention required",
    "verify you are human",
    "are you a robot",
    "security service to protect against malicious bots",
    "access denied",
    "please enable javascript to proceed",
)

_local = threading.local()


def sess() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA,
                          "Accept-Language": "en-US,en;q=0.9"})
        _local.s = s
    return s


def is_wall(text: str) -> bool:
    low = (text or "")[:3000].lower()
    return any(m in low for m in WALL_MARK)


# ---------------- ① 壳页 -> signature / timestamp ----------------
def gn_sig(art_id: str, timeout: int):
    r = sess().get("https://news.google.com/rss/articles/" + art_id,
                   timeout=timeout, allow_redirects=True)
    if r.status_code != 200:
        return None, None, "壳页 HTTP {}".format(r.status_code)
    html = r.text
    sg = re.search(r'data-n-a-sg="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    if not sg or not ts:
        return None, None, "壳页里没有 sg/ts（{}B）".format(len(html))
    return sg.group(1), ts.group(1), ""


# ---------------- ② batchexecute 解码 ----------------
def gn_decode(art_id: str, sg: str, ts: str, timeout: int):
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
               None, None, None, None, None, 0, 1],
              "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             art_id, int(ts), sg]
    req = ["Fbv4je", json.dumps(inner, separators=(",", ":"))]
    payload = "f.req=" + quote(json.dumps([[req]]))
    r = sess().post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        headers={"content-type":
                 "application/x-www-form-urlencoded;charset=UTF-8"},
        data=payload, timeout=timeout)
    for line in r.text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        for item in data:
            if (isinstance(item, list) and len(item) > 2
                    and item[1] == "Fbv4je"):
                try:
                    inn = json.loads(item[2])
                except Exception:
                    continue
                if inn and inn[0] == "garturlres":
                    return inn[1], ""
    return None, "batchexecute 未返回 garturlres（HTTP {}）".format(r.status_code)


# ---------------- ③ 抽正文 ----------------
_TRAFILATURA = None


def extract(html: bytes, url: str) -> str:
    global _TRAFILATURA
    if _TRAFILATURA is None:
        try:
            import trafilatura
            _TRAFILATURA = trafilatura
        except Exception:
            _TRAFILATURA = False
    if not _TRAFILATURA:
        return ""
    try:
        return (_TRAFILATURA.extract(html, url=url, output_format="txt",
                                     include_comments=False,
                                     include_tables=True) or "").strip()
    except Exception:
        return ""


def one(rec: dict, timeout: int) -> dict:
    """处理一条。返回结果 dict（含 stage 与 err，便于统计）。"""
    aid = str(rec.get("article_id") or "")
    url = str(rec.get("url") or "")
    out = {"article_id": rec.get("article_id"), "recv_url": url,
           "final_url": "", "ok": False, "stage": "", "err": "",
           "chars": 0, "text": ""}
    m = re.search(r"/articles/([^?]+)", url)
    if not m:
        out["err"] = "不是 /articles/ 形态"
        return out
    art = m.group(1)

    sg, ts, err = gn_sig(art, timeout)
    if not sg:
        out["err"] = err
        return out
    out["stage"] = "sig"
    final, err = gn_decode(art, sg, ts, timeout)
    if not final:
        out["err"] = err
        return out
    out["stage"] = "decoded"
    out["final_url"] = final

    try:
        r = sess().get(final, timeout=timeout, allow_redirects=True)
    except Exception as e:
        out["err"] = "抓正文 {}: {}".format(type(e).__name__, str(e)[:60])
        return out
    if r.status_code != 200:
        out["err"] = "正文 HTTP {}".format(r.status_code)
        return out
    text = extract(r.content, final)
    if len(text) < MIN_TEXT:
        out["err"] = ("拦截/空页（{}字）".format(len(text))
                      if is_wall(text) else "正文仅 {} 字".format(len(text)))
        return out
    if is_wall(text):
        out["err"] = "拦截页文案"
        return out
    out.update(ok=True, stage="full", chars=len(text), text=text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", default="data/pending_google.json")
    ap.add_argument("--out", default="data/gnews_content.jsonl")
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=30, help="单次请求超时/秒")
    ap.add_argument("--budget-min", type=int, default=60, help="整体预算/分钟")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not os.path.exists(args.pending):
        print("缺少 {}".format(args.pending), file=sys.stderr)
        return 2
    recs = json.load(open(args.pending, encoding="utf-8"))

    # 只处理 google 跳转链（bing 的另走一站内搜索路径）
    recs = [r for r in recs if "news.google.com" in str(r.get("url") or "")]

    done_ids = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                done_ids.add(str(json.loads(line).get("article_id")))
            except Exception:
                pass
    todo = [r for r in recs if str(r.get("article_id")) not in done_ids]
    if args.limit:
        todo = todo[:args.limit]
    print("[gnews] 清单 {} 条 / 已完成 {} / 本轮 {} 条 / 并发 {} / 预算 {} 分".format(
        len(recs), len(done_ids), len(todo), args.conc, args.budget_min),
        flush=True)
    if not todo:
        print("[gnews] 无待办")
        return 0

    deadline = time.time() + args.budget_min * 60 if args.budget_min else 0
    lock = threading.Lock()
    stat = {"decoded": 0, "sig_fail": 0}

    fout = open(args.out, "a", encoding="utf-8")
    t0 = time.time()
    n_ok = 0

    def run(r):
        if deadline and time.time() > deadline:
            return None
        return one(r, args.timeout)

    with ThreadPoolExecutor(max_workers=args.conc) as ex:
        futs = {ex.submit(run, r): r for r in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                res = f.result()
            except Exception as e:
                res = {"article_id": futs[f].get("article_id"), "ok": False,
                       "err": "{}: {}".format(type(e).__name__, str(e)[:60]),
                       "stage": "", "chars": 0, "text": "",
                       "recv_url": futs[f].get("url"), "final_url": ""}
            if res is None:
                print("  ⏹ 到达整体预算，剩余任务不再派发", flush=True)
                break
            with lock:
                # 逐条落盘：中途被 kill 也不丢已完成的部分
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                if res.get("stage"):
                    stat["decoded"] += 1
                else:
                    stat["sig_fail"] += 1
                if res.get("ok"):
                    n_ok += 1
                flag = "✓" if res.get("ok") else "✗"
                print("  [{:>4}/{}] {} {:>6}字 {:<8} {}".format(
                    i, len(todo), flag, res.get("chars", 0),
                    str(res.get("stage") or "-"),
                    (res.get("final_url") or res.get("err") or "")[:78]),
                    flush=True)
    fout.close()

    print("[gnews] 完成：成功 {} 条，耗时 {:.0f}s，"
          "解码段失败 {} 条，输出 {}".format(
              n_ok, time.time() - t0, stat["sig_fail"], args.out), flush=True)

    # 汇总（工作流里也读一次，便于看板）
    from collections import Counter
    errs = Counter()
    for line in open(args.out, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not d.get("ok"):
            errs[re.sub(r"\d+", "N", str(d.get("err") or "?"))[:46]] += 1
    print("[gnews] 失败原因 top:", dict(errs.most_common(8)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
