#!/usr/bin/env python3
"""海外信源可达性探针（跑在 GitHub Actions / Azure 机房）。

目的：回答一个单纯的问题 —— 从 Azure 出口 IP 出发，
      这些对腾讯云 VPS 返回 403 / Cloudflare 挑战的站点，能不能拿到内容。

不采集、不入库、不翻译，只输出一张「谁通、谁不通、出口IP是什么」的诊断表。
探针结果用于决定 friction 站要不要走 Actions 这条路。
"""
import json
import os
import socket
import sys
from datetime import datetime, timezone

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 重点验证对象：就是 VPS 上被 WAF 拦住的那批
TARGETS = [
    # (信源名, URL, 站点类型)
    ("Counterman",        "https://www.counterman.com/",            "CF企业级"),
    ("Tire Business",     "https://www.tirebusiness.com/",          "Akamai"),
    ("Fleet Owner",       "https://www.fleetowner.com/",            "CF企业级"),
    ("CCJ Digital",       "https://www.ccjdigital.com/",            "CF企业级"),
    ("Underhood Service", "https://www.underhoodservice.com/",      "CF企业级"),
    ("Aftermarket News",  "https://www.aftermarketnews.com/",       "CF企业级"),
    ("TMD Friction",      "https://www.tmdfriction.com/en/news",    "CF企业级"),
    ("UNECE WP.29",       "https://unece.org/transport/vehicle-regulations", "CF企业级"),
    ("Automotive News EU","https://www.autonews.com/europe/",       "Akamai"),
    ("Espacenet",         "https://worldwide.espacenet.com/",       "CF企业级"),
    # 对照组：VPS 上能通的，确认 Actions 也通（排除环境问题）
    ("SAE 新闻",           "https://www.sae.org/news",              "对标-应通"),
    ("US EPA",            "https://www.epa.gov/newsreleases",       "对标-应通"),
    ("Bosch 新闻室",       "https://www.bosch-presse.de/pressportal/de/en/", "对标-应通"),
]


def classify(resp):
    """把响应归类。"""
    if resp is None:
        return "连接失败", False
    low = resp.text[:4000].lower()
    if "just a moment" in low or "cf-browser-verification" in low:
        return "Cloudflare 挑战", False
    if "access denied" in low or "reference #" in low:
        return "WAF 拒绝", False
    if resp.status_code == 403:
        return "403 Forbidden", False
    if resp.status_code == 429:
        return "429 限流", False
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}", False
    if len(resp.content) < 3000:
        return f"疑似空页({len(resp.content)}B)", False
    return "OK", True


def probe(name, url, kind):
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
        verdict, ok = classify(r)
        return {
            "name": name, "url": url, "kind": kind,
            "status": r.status_code, "bytes": len(r.content),
            "verdict": verdict, "ok": ok,
        }
    except Exception as e:
        return {
            "name": name, "url": url, "kind": kind,
            "status": None, "bytes": 0,
            "verdict": f"异常: {repr(e)[:70]}", "ok": False,
        }


def main():
    out = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "runner": {
            "hostname": socket.gethostname(),
            "egress_ip": None,
        },
        "results": [],
    }
    # 出口 IP 很重要：用于对比 Azure 段与腾讯云段
    for ipapi in ("https://api.ipify.org?format=json",
                  "https://ifconfig.me/ip",
                  "https://icanhazip.com"):
        try:
            r = requests.get(ipapi, timeout=12)
            if r.status_code == 200:
                txt = r.text.strip()
                if txt.startswith("{"):
                    out["runner"]["egress_ip"] = json.loads(txt).get("ip")
                else:
                    out["runner"]["egress_ip"] = txt
                break
        except Exception:
            continue

    for name, url, kind in TARGETS:
        res = probe(name, url, kind)
        out["results"].append(res)
        mark = "✅" if res["ok"] else "⛔"
        print(f"{mark} {name:<20} {res['verdict']:<22} {res['bytes']:>7}B  {res['status']}")

    ok_n = sum(1 for r in out["results"] if r["ok"])
    waf_n = sum(1 for r in out["results"]
                if "Cloudflare" in r["verdict"] or "WAF" in r["verdict"])
    # 对照组（VPS 上本来就通的）不计入"突破"判定，只看 WAF 组的结果
    ctrl = [r for r in out["results"] if "对标" in r["kind"]]
    waf_targets = [r for r in out["results"] if "对标" not in r["kind"]]
    waf_ok = sum(1 for r in waf_targets if r["ok"])
    ctrl_ok = sum(1 for r in ctrl if r["ok"])
    if waf_ok >= 3:
        verdict = f"✅ WAF 组突破 {waf_ok} 个 —— Azure IP 有效，可以照此搭建采集"
    elif waf_ok >= 1:
        verdict = f"🔶 仅突破 {waf_ok} 个 —— 部分有效，建议只保留能通的那几站"
    else:
        verdict = "⛔ WAF 组全部被拦 —— Azure IP 同样无效，需改用住宅代理或第三方聚合"
    out["summary"] = {
        "total": len(out["results"]),
        "ok": ok_n,
        "blocked": len(out["results"]) - ok_n,
        "waf": waf_n,
        "waf_group_total": len(waf_targets),
        "waf_group_ok": waf_ok,
        "control_group_ok": f"{ctrl_ok}/{len(ctrl)}",
        "verdict": verdict,
    }

    os.makedirs("out", exist_ok=True)
    with open("out/probe_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 62)
    print(f"出口 IP   : {out['runner']['egress_ip']}")
    print(f"对照组    : {ctrl_ok}/{len(ctrl)} 通（这些 VPS 上本来就通，用于排除环境问题）")
    print(f"WAF 组    : {waf_ok}/{len(waf_targets)} 通 ← 这才是关键指标")
    print(f"判定      : {verdict}")
    print("=" * 62)
    # 探针本身永远退出 0，避免因目标站不可达而让工作流变红
    return 0


if __name__ == "__main__":
    sys.exit(main())
