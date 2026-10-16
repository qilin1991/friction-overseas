# -*- coding: utf-8 -*-
"""探测 GitHub Actions（Azure 出口）能否访问 Google Scholar 搜索结果。

背景：本机直连 scholar.google.com = 000（被墙）；VPS 出口同样被墙；
镜像站 xueshu.lanfanshu.cn 有登录墙。唯一未验证通道 = GH Actions。

判定标准：
  - HTTP 200 + 页面含 `class="gs_r gs_or"`（标准结果块）≥ 5 → 可采集
  - 出现 /sorry/、`detected unusual traffic`、`gs_captcha` → 被反爬
输出 out/gs_probe.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
BASE = "https://scholar.google.com"


def fetch(url, cookie=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
         "Accept-Language": "en-US,en;q=0.9"}
    if cookie:
        h["Cookie"] = cookie
    r = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, ("ERR " + repr(e)).encode(), {}


def analyze(status, body):
    txt = body.decode("utf-8", "replace")
    n_res = len(re.findall(r'class="gs_r gs_or', txt)) or len(re.findall(r'class="gs_ri"', txt))
    n_snip = len(re.findall(r'class="gs_rs"', txt))
    n_a = len(re.findall(r'class="gs_a"', txt))
    captcha = any(k in txt for k in (
        "detected unusual traffic", "/sorry/", "gs_captcha",
        "unusual traffic from your computer", "not a robot"))
    return {
        "status": status, "bytes": len(body),
        "results": n_res, "snippets": n_snip, "authors": n_a,
        "captcha": captcha,
    }


def main():
    out = []
    # ① 首页（取 cookie）
    st, b, hd = fetch(BASE + "/scholar?hl=en")
    ck = hd.get("Set-Cookie", "")
    m = re.search(r"(GSP=.*?);", ck)
    cookie = m.group(1) if m else ck.split(";")[0] if ck else ""
    out.append({"step": "home", **analyze(st, b), "cookie": bool(cookie)})
    print("home:", out[-1], flush=True)
    time.sleep(6)

    # ② 第一次查询
    q = urllib.parse.quote("brake pad friction material")
    st, b, hd = fetch(BASE + "/scholar?q=%s&hl=en" % q, cookie)
    a = analyze(st, b)
    out.append({"step": "q1", **a})
    print("q1:", a, flush=True)
    if a["captcha"] or a["results"] == 0:
        json.dump(out, open("out/gs_probe.json", "w"), ensure_ascii=False, indent=1)
        print("EARLY_STOP", flush=True)
        return
    time.sleep(10)

    # ③ 换词第二次查询（验证非首页特例）
    q2 = urllib.parse.quote("copper-free brake pad wear")
    st, b, hd = fetch(BASE + "/scholar?q=%s&hl=en" % q2, cookie)
    a2 = analyze(st, b)
    out.append({"step": "q2", **a2})
    print("q2:", a2, flush=True)
    time.sleep(10)

    # ④ 第 2 页翻页测试
    st, b, hd = fetch(BASE + "/scholar?q=%s&hl=en&start=10" % q, cookie)
    a3 = analyze(st, b)
    out.append({"step": "p2", **a3})
    print("p2:", a3, flush=True)

    import os
    os.makedirs("out", exist_ok=True)
    json.dump(out, open("out/gs_probe.json", "w"), ensure_ascii=False, indent=1)
    ok = all(o.get("results", 0) >= 5 and not o.get("captcha") for o in out[1:])
    print("VERDICT:", "GS_OK" if ok else "GS_BLOCKED", flush=True)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
