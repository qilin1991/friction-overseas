#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：验证 batchexecute 解码，并把原始响应落盘供分析。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolve_gnews as R  # noqa: E402

import requests  # noqa: E402


def main():
    os.makedirs("out", exist_ok=True)
    n = int(os.environ.get("N", "3"))
    items = []
    with open(R.IN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "|" in line:
                a, b = line.split("|", 1)
                items.append((int(a), b.strip()))
            if len(items) >= n:
                break

    s = requests.Session()
    report = []
    for idx, url in items:
        aid = R.extract_id(url)
        inner = json.dumps([[[["X", "X", [aid]]]], None, None])
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        rec = {"id": idx, "article_id": aid}
        try:
            r = s.post(R.BATCH_URL, data={"f.req": freq}, timeout=25,
                       headers={
                           "User-Agent": R.UA,
                           "Content-Type":
                               "application/x-www-form-urlencoded;charset=UTF-8",
                           "Accept-Language": "en-US,en;q=0.9",
                           "Origin": "https://news.google.com",
                           "Referer": "https://news.google.com/",
                       })
            rec["status"] = r.status_code
            rec["len"] = len(r.text)
            rec["head"] = r.text[:2500]
            p = "out/batch_raw_%s.txt" % idx
            with open(p, "w", encoding="utf-8") as f:
                f.write(r.text)
            rec["saved"] = p
            rec["parsed"] = R.parse_batch_response(r.text)[:5]
            # 直接在原文里搜 URL
            import re
            rec["literal_urls"] = re.findall(r'https?://[^\s"\\]+',
                                             r.text)[:10]
        except Exception as e:
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        report.append(rec)

        print("=" * 62)
        print("id:", idx, "| status:", rec.get("status"),
              "| len:", rec.get("len"))
        print("article_id:", (aid or "")[:70])
        if rec.get("error"):
            print("ERROR:", rec["error"])
        print("parsed:", json.dumps(rec.get("parsed", []),
                                    ensure_ascii=False)[:300])
        print("literal_urls:", json.dumps(rec.get("literal_urls", []),
                                          ensure_ascii=False)[:400])
        print("head:", (rec.get("head") or "").replace("\n", "|")[:600])

    with open("out/gnews_batch_diag.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
