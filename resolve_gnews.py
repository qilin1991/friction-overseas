#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google News 中转链接解码（基于内部 batchexecute 接口）。

背景与原理
----------
news.google.com/rss/articles/<加密串> 这个地址，Google 早就不是用 302 跳转了：
  · 不带 cookie 直接 GET，返回 302 指向它自己
  · 跟随后拿到 200，但那是 594KB 的 Angular SPA，HTML 里**一个外部域名都没有**
  · 真实链接靠前端 JS 调 Google 内部接口异步取

那个内部接口是：
  POST https://news.google.com/_/DotsSplashUi/data/batchexecute
  body: f.req=[[["Fbv4je", "<json>", null, "generic"]]]
  其中 <json> = json.dumps([[[["X","X",["<article_id>"]]]], None, None])
  article_id 就是 URL 里 /articles/ 后面那串（?oc=5 之类的查询串要保留）

响应是 Google 特有的 ")]}'" 前缀 + 多段 JSON 数组，里面带真实 URL。

注意
----
这是 Google News 前端自己用的接口，属于非公开 API，随时可能变。
脚本对解析做了容错：拿不到就如实标记失败，不猜。
"""
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

IN_FILE = "gnews_pending.txt"
OUT_FILE = "gnews_resolved.jsonl"
CONC = int(os.environ.get("CONC", "8"))
TIMEOUT = int(os.environ.get("TMO", "20"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
TRACKING = {
    "oc", "hl", "gl", "ceid", "cs", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "fbclid", "gclid", "cmpid", "cid",
}


def clean(url):
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        sp = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(sp.query, keep_blank_values=True)
             if k.lower() not in TRACKING]
        return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), ""))
    except Exception:
        return url


def is_gnews(u):
    return "news.google.com" in u or "google.com/rss/articles" in u


def extract_id(url):
    """从 URL 里抠出 article id（含查询串，Google 要求原样传）。"""
    m = re.search(r"/articles/([^/?#]+)(\?[^#]*)?", url)
    if not m:
        return None
    return m.group(1) + (m.group(2) or "")


def _walk(node, depth=0):
    """递归遍历，收集所有看起来像原文的 URL。"""
    out = []
    if depth > 12:
        return out
    if isinstance(node, str):
        if node.startswith("http") and not is_gnews(node):
            if not re.match(r"https?://(www\.)?(google|gstatic)\.", node):
                out.append(node)
    elif isinstance(node, list):
        for x in node:
            out.extend(_walk(x, depth + 1))
    elif isinstance(node, dict):
        for x in node.values():
            out.extend(_walk(x, depth + 1))
    return out


def parse_batch_response(text):
    """解析 batchexecute 响应，抠出真实 URL。

    响应形如：
      )]}'
      <length>
      [["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://real-url\\",...]",null,null,null,"generic"]]
      ...
    """
    body = text[4:] if text.startswith(")]}'") else text
    cands = []
    for chunk in body.split("\n"):
        chunk = chunk.strip()
        if not chunk or not chunk.startswith("["):
            continue
        try:
            arr = json.loads(chunk)
        except Exception:
            continue
        for row in arr:
            if not isinstance(row, list) or len(row) < 3:
                continue
            inner = row[2]
            if not isinstance(inner, str):
                continue
            try:
                payload = json.loads(inner)
            except Exception:
                for u in re.findall(r'https?://[^\s"\\]+', inner):
                    cands.append(u)
                continue
            cands.extend(_walk(payload))
    return cands


def resolve_one(item, session):
    idx, url = item
    orig = url
    aid = extract_id(url)
    if not aid:
        return False, orig, "no_article_id"

    inner = json.dumps([[[["X", "X", [aid]]]], None, None])
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])

    try:
        r = session.post(
            BATCH_URL, data={"f.req": freq}, timeout=TIMEOUT,
            headers={
                "User-Agent": UA,
                "Content-Type":
                    "application/x-www-form-urlencoded;charset=UTF-8",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://news.google.com",
                "Referer": "https://news.google.com/",
            })
        if r.status_code != 200:
            return False, orig, "http_%s" % r.status_code
        cands = parse_batch_response(r.text)
        real = [c for c in cands if not is_gnews(c)]
        if not real:
            return False, orig, "no_url_in_response"
        return True, clean(real[0]), "ok"
    except requests.exceptions.Timeout:
        return False, orig, "timeout"
    except Exception as e:
        return False, orig, type(e).__name__


def main():
    if not os.path.exists(IN_FILE):
        print("找不到", IN_FILE, file=sys.stderr)
        return 1

    items = []
    with open(IN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "|" in line:
                a, b = line.split("|", 1)
                try:
                    items.append((int(a), b.strip()))
                except ValueError:
                    continue

    print("待解码：%d 条，并发 %d，超时 %ds" % (len(items), CONC, TIMEOUT))

    ok_n = 0
    results = []
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        futs = {ex.submit(resolve_one, it, requests.Session()): it
                for it in items}
        done = 0
        for fu in as_completed(futs):
            idx, orig = futs[fu]
            try:
                ok, final, note = fu.result()
            except Exception as e:
                ok, final, note = False, orig, type(e).__name__
            ok_n += 1 if ok else 0
            results.append({"id": idx, "orig": orig, "final": final,
                            "ok": ok, "note": note})
            done += 1
            if done % 25 == 0 or done == len(items):
                print("  进度 %d/%d  成功 %d" % (done, len(items), ok_n))
            time.sleep(0.02)

    results.sort(key=lambda r: r["id"])
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fail = Counter(r["note"] for r in results if not r["ok"])
    print("完成：成功 %d / 失败 %d / 共 %d"
          % (ok_n, len(results) - ok_n, len(results)))
    if fail:
        print("失败原因：", dict(fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
