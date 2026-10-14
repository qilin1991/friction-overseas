# -*- coding: utf-8 -*-
"""第三轮探测：中文库的 XHR 接口（HTML 都是 SPA 壳，必须找 API）。

已知：
  wanfang_paper   200 / 171KB 但 0 条真实条目（Vue 壳）
  cnki_oversea    200 / 53KB  壳页，但**入口可达** ← 值得挖
  xmol            200 / 59KB  Next.js 壳
  cqvip           412         被拦
输出 out/cn_api_probe.json
"""
import json
import os
import re
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE_HDR = {
    "User-Agent": UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}


def show(tag, r):
    body = r.text or ""
    print("  [%s] HTTP %s size=%s ct=%s" % (tag, r.status_code, len(body),
                                            r.headers.get("Content-Type", "")[:40]), flush=True)
    print("      ", re.sub(r"\s+", " ", body[:220]), flush=True)
    return body


def main():
    os.makedirs("out", exist_ok=True)
    s = requests.Session()
    s.headers.update(BASE_HDR)
    log = []

    # ---------- 1. 万方 XHR ----------
    print("=== 万方 ===", flush=True)
    try:
        h = dict(BASE_HDR)
        h["Content-Type"] = "application/json"
        h["Origin"] = "https://s.wanfangdata.com.cn"
        h["Referer"] = "https://s.wanfangdata.com.cn/paper?q=%E5%88%B9%E8%BD%A6%E7%89%87"
        payload = {"searchType": "paper", "searchWord": "刹车片",
                   "pageSize": 20, "page": 1, "order": "correlation"}
        for path in ("/SearchService.SearchService.searchperfect.aspx",
                     "/SearchService.SearchService.search.aspx"):
            r = s.post("https://s.wanfangdata.com.cn" + path, json=payload, headers=h, timeout=25)
            b = show("wanfang" + path.split(".")[-2], r)
            log.append({"tag": "wanfang:" + path, "code": r.status_code, "size": len(b), "body": b[:600]})
            time.sleep(1.5)
    except Exception as e:
        print("  !! wanfang", e, flush=True)

    # ---------- 2. 知网海外 ----------
    print("\n=== 知网海外 (chn.oversea.cnki.net) ===", flush=True)
    try:
        s2 = requests.Session()
        s2.headers.update(BASE_HDR)
        home = s2.get("https://chn.oversea.cnki.net/kns/defaultresult/index", timeout=25)
        print("  home:", home.status_code, len(home.text), "| cookies:", len(s2.cookies), flush=True)
        # 常见检索接口
        cands = [
            ("GET", "https://chn.oversea.cnki.net/kns/brief/grid?dbcode=CJFD&kw=" + urllib.parse.quote("刹车片"), None),
            ("POST", "https://chn.oversea.cnki.net/kns/brief/grid", {"kw": "刹车片", "dbcode": "CJFD", "page": 1}),
            ("POST", "https://chn.oversea.cnki.net/kns8s/brief/grid", {"boolSearch": "true", "QueryJson": json.dumps({"Platform": "", "Resource": "CROSSDB", "Classid": "WD0FTY92", "Products": "", "QNode": {"QGroup": [{"Key": "Subject", "Title": "", "Logic": 0, "Items": [{"Title": "主题", "Name": "SU", "Value": "刹车片", "Operate": "=", "BlurType": ""}], "ChildItems": []}]}, "ExScope": "1", "SearchType": 4, "Rlang": "CHINESE", "KuaKuCode": ""})}),
        ]
        for method, url, body in cands:
            try:
                if method == "GET":
                    r = s2.get(url, timeout=25)
                else:
                    r = s2.post(url, data=body, timeout=25,
                                headers={**BASE_HDR, "Content-Type": "application/x-www-form-urlencoded"})
                b = show("cnki_" + url.rsplit("/", 1)[-1][:18], r)
                log.append({"tag": "cnki:" + url, "code": r.status_code, "size": len(b), "body": b[:600]})
            except Exception as e:
                print("   !!", url, e, flush=True)
            time.sleep(1.5)
    except Exception as e:
        print("  !! cnki", e, flush=True)

    # ---------- 3. X-MOL ----------
    print("\n=== X-MOL ===", flush=True)
    try:
        for url, body in (
            ("https://www.x-mol.com/api/paper/search", {"q": "brake friction", "page": 1}),
            ("https://www.x-mol.com/paper/search/q?option=brake%20friction", None),
        ):
            r = s.post(url, json=body, timeout=25) if body else s.get(url, timeout=25)
            b = show("xmol", r)
            log.append({"tag": "xmol:" + url, "code": r.status_code, "size": len(b), "body": b[:400]})
            time.sleep(1.5)
    except Exception as e:
        print("  !! xmol", e, flush=True)

    # ---------- 4. 知网能否直接用「检索结果页」URL ----------
    print("\n=== 知网结果页直取 ===", flush=True)
    try:
        u = ("https://chn.oversea.cnki.net/kns/defaultresult/index?kw="
             + urllib.parse.quote("刹车片") + "&korder=SU")
        r = s.get(u, timeout=25)
        b = show("cnki_result", r)
        log.append({"tag": "cnki_result", "code": r.status_code, "size": len(b), "body": ""})
        m = re.findall(r"result-table-list|kns8s|litterature|<tr[^>]*>", b)
        print("      结构线索:", set(m[:5]), flush=True)
    except Exception as e:
        print("  !!", e, flush=True)

    json.dump({"rows": log}, open("out/cn_api_probe.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nCN_API_PROBE_DONE")


if __name__ == "__main__":
    main()
