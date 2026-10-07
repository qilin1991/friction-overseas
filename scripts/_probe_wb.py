#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探针（WorkBuddy 2026-10-07）：验证 GitHub Actions 的 Azure IP 能否

  ① 直接访问那些对 VPS 返 403 的发布站（Cloudflare 按 IP 挡）
  ② 用 batchexecute 接口解码 news.google.com 的 AU_yqL 跳转链
  ③ 抓到解码后的正文（trafilatura）

只读，不写仓库任何数据文件。跑完即可删除本文件与 _probe_wb.yml。
"""
import json
import re
import sys
import time
from urllib.parse import quote

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

# ---------------- ① 发布站可达性 ----------------
PUBLISHERS = [
    "https://www.counterman.com/?s=brake+pad",
    "https://www.import-car.com/?s=brake",
    "https://www.thebrakereport.com/?s=brake",
    "https://www.bikerumor.com/?s=brake",
    "https://www.road.cc/?s=brake",
    "https://www.automotiveworld.com/?s=brake",
    "https://news.google.com/rss/search?q=brake+pad&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/",
]

# ---------------- ② 待解码的 Google News 文章 ----------------
GARTICLES = [
    {"dbid": 938, "aid": "CBMimwFBVV95cUxPMHoyUFlwRGhwU3FfaUhZQnpKeHZ5dkVXSVdwcEprUWE3Qlh1ZlpnRnZZRDdTMmRlYmJTM0x1TGhSUEE0c1hHelZ0WU0tcHdtSmloVFpUUjAwMnV6cW5KZFFka0liUTV3ZER0eUVFenR6WS1KSExndGRfZXZUbmJvb2hlNmd2X2FlM2NSVm1rMXlZWG1OTFpzb1NmTQ"},
    {"dbid": 939, "aid": "CBMiqgFBVV95cUxNTUNJT2tkSW91TUxfMTlRczhobVJCTG1yTExyY2FfMTRzWlVUZU4tM0ZBbTJKSk1Ba3Q5b05JVVRtVDRGbUhCQlI1dC13dE11RHJHdVhBMHhPT2Y1dm5wc3FYQTdNVGVfQTB2QURjekVUeTgtRkd0bDMtM2RSNXhXRXpUVS14MnR6aGxXU082WWk2RUx5Z3Nna0hpNHQ0SVpoNko5T0JKUlc0QQ"},
    {"dbid": 940, "aid": "CBMieEFVX3lxTE1NVTRyVE80NU5OWmVuS0Rvc0FOT3VGXzgzR0xDQXdZSjdJRjBLSUl2a3BlbHZ0ZlQ0ZkxrdkEtNk0zT0pfanFRYW53aFkwYWY3M2FULTBCempSM0RvTDMzdUg0ajh5SzBJQ3Bpc0hXcW93VkhkRVE5Tg"},
    {"dbid": 941, "aid": "CBMiywFBVV95cUxNeEZqR00zZWpYLVNNalB0eUJEUTZESjIzclRpb0VwSnBpYWhKOXFMS0wtQUFnYmtOTlFoNEh1dmFNOTZzNmRsZ2tHaUI2ZG5Ec2V5Q1h4Y3NvSHBqR3RKYmVjRE9EZE51ZTU3dTh3OFdyeVAwNDRmSjkyZ3FlVUtQM2s0bHFmdzFSQkpSSEZTalRSbUpycjNtUjM5ZVNVcGpPZ3FpM2o0MGRaZ1M5YzZVbFlKZjcxQ3R2RFNVNjJqZFljNlJpdzU5X2g0WdIB0AFBVV95cUxNeHdpYXhWOUlOZ2RKbExOR0lXNDdmV3dnYVdTbFFCWThYbm94Y2tvZnh4NUZtbjZzVjA3eWFxM2J1amZBZXZYRW1YVDZwUDBkLWJJRU9JcGRVOVE0VjRhT0F5d2w1dFJWdG5CZUYtUTB0bUtRZDJKMDE3Q0pZVGx2WmJKOU9xbnRBZHhzWEZaQjFZdUMtTDl1bWFsdzRINEQ4NjNxa3Q3azJKTWhQX1M5MnBoRlJmQzlHSHRJc21UMUl5SjNHaXF6Vnhqc0FMaE5q"},
    {"dbid": 942, "aid": "CBMijgFBVV95cUxNdEl4alZhUmtrM3lRQ09HNnpQVkFFckJja0t2R1U1dlRKVFNhWWJ1VVAxLXlNZDMwaGxEUXZQd1ozVEhrQWtJSDFEWEJ5elJuZTdxdnBRaWdrMURWc1pYaEVhUkpXN3pMU0o5cTVERVJTY2tLa2RoWmpyS3ZLM1M1YW42VDM4MTV3Ni1IQjN3"},
]

CRLF = "\n\n"


def sec(t):
    print("\n" + "=" * 6 + " " + t + " " + "=" * 6, flush=True)


# ---------------- ① ----------------
sec("① 发布站可达性（本 runner 的出口 IP）")
try:
    ip = S.get("https://api.ipify.org", timeout=15).text.strip()
    print("出口 IP:", ip, flush=True)
except Exception as e:
    print("取 IP 失败:", e, flush=True)
for u in PUBLISHERS:
    t = time.time()
    try:
        r = S.get(u, timeout=25, allow_redirects=True)
        print("  {:>4}  {:>9}B  {:>5.1f}s  {}".format(
            r.status_code, len(r.content), time.time() - t, u), flush=True)
    except Exception as e:
        print("  ERR  {}: {}  {}".format(
            type(e).__name__, str(e)[:50], u), flush=True)


# ---------------- ② ----------------
def gn_sig(art_id):
    """从文章壳页里取出 batchexecute 需要的 signature / timestamp。"""
    r = S.get("https://news.google.com/rss/articles/" + art_id,
              timeout=25, allow_redirects=True)
    html = r.text
    sg = re.search(r'data-n-a-sg="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    return (sg.group(1) if sg else None,
            ts.group(1) if ts else None,
            len(html))


def gn_decode(art_id, sg, ts):
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
               None, None, None, None, None, 0, 1],
              "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             art_id, int(ts), sg]
    req = ["Fbv4je", json.dumps(inner, separators=(",", ":"))]
    payload = "f.req=" + quote(json.dumps([[req]]))
    r = S.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
               headers={"content-type":
                        "application/x-www-form-urlencoded;charset=UTF-8"},
               data=payload, timeout=30)
    raw = r.text
    for line in raw.splitlines():
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
                    return inn[1], raw[:200]
    return None, raw[:300]


sec("② Google News 跳转链解码（batchexecute）")
resolved = []
for a in GARTICLES:
    try:
        sg, ts, hl = gn_sig(a["aid"])
        print("  dbid={} 壳页 {}B  sg={}  ts={}".format(
            a["dbid"], hl, (sg or "-")[:12], ts or "-"), flush=True)
        if not sg or not ts:
            print("     ✗ 取不到 signature/timestamp", flush=True)
            continue
        url, head = gn_decode(a["aid"], sg, ts)
        if url:
            print("     ✓ -> {}".format(url[:110]), flush=True)
            resolved.append((a["dbid"], url))
        else:
            print("     ✗ 解码失败，响应头: {}".format(
                head.replace("\n", " ")[:160]), flush=True)
    except Exception as e:
        print("     ERR {}: {}".format(type(e).__name__, str(e)[:70]),
              flush=True)


# ---------------- ③ ----------------
sec("③ 解码后正文抓取（trafilatura）")
try:
    import trafilatura
    HAVE_T = True
except Exception as e:
    print("trafilatura 不可用:", e)
    HAVE_T = False
for dbid, url in resolved:
    try:
        r = S.get(url, timeout=30, allow_redirects=True)
        body = ""
        if HAVE_T:
            body = trafilatura.extract(r.content, url=url, output_format="txt",
                                       include_comments=False,
                                       include_tables=True) or ""
        print("  dbid={}  HTTP {}  {}B  正文 {} 字  {}".format(
            dbid, r.status_code, len(r.content), len(body), url[:70]),
            flush=True)
        if body:
            print("     开头: {}".format(body[:90].replace("\n", " ")),
                  flush=True)
    except Exception as e:
        print("  dbid={} ERR {}: {}".format(dbid, type(e).__name__,
                                            str(e)[:60]), flush=True)

print("\n[probe] 完成", flush=True)
sys.exit(0)
