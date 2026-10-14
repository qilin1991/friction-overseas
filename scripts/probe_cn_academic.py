# -*- coding: utf-8 -*-
"""第二轮探测：中文文献源可解析性（万方已确认 Azure 出口 200）。

目标：确认能不能真解析出条目，而不是只拿到壳页。
输出 out/cn_probe.json
"""
import json
import os
import re
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TARGETS = [
    # 万方（上一轮 200 / 171KB）
    ("wanfang_paper",   "https://s.wanfangdata.com.cn/paper?q=" + urllib.parse.quote("刹车片"),
     {"Referer": "https://www.wanfangdata.com.cn/"}),
    ("wanfang_thesis",  "https://s.wanfangdata.com.cn/thesis?q=" + urllib.parse.quote("摩擦材料"),
     {"Referer": "https://www.wanfangdata.com.cn/"}),
    # 万方可能的 JSON 接口
    ("wanfang_api",     "https://s.wanfangdata.com.cn/SearchService.SearchService.searchperfect.aspx?"
                        + urllib.parse.urlencode({"searchWord": "刹车片", "pageSize": 20, "page": 1}),
     {"Referer": "https://s.wanfangdata.com.cn/", "X-Requested-With": "XMLHttpRequest"}),
    # 维普（上一轮 412，加 Referer 再试）
    ("cqvip",           "https://qikan.cqvip.com/Qikan/Search/Index?" + urllib.parse.urlencode({"key": "刹车片"}),
     {"Referer": "https://qikan.cqvip.com/"}),
    # 知网海外入口（Azure 出口或许可达）
    ("cnki_oversea",    "https://chn.oversea.cnki.net/kns/defaultresult/index",
     {}),
    # X-MOL 学术平台
    ("xmol",            "https://www.x-mol.com/paper/search/q?option=" + urllib.parse.quote("brake friction"),
     {}),
    # 百度学术 移动端（换入口或许绕开 PC 端验证码）
    ("baidu_xueshu_m",  "https://xueshu.baidu.com/s?wd=" + urllib.parse.quote("刹车片") + "&ie=utf-8&tn=SE_baiduxueshu_c1gjeupa",
     {"Referer": "https://xueshu.baidu.com/"}),
    # 中文期刊开放平台
    ("cnki_open",       "https://kns.cnki.net/kcms2/article/abstract?v=test",
     {"Referer": "https://kns.cnki.net/"}),
]

# 判定线索：(正则, 含义)
SIGNALS = {
    "wanfang": [("__INITIAL_STATE__|window\\.__NUXT__", "SSR 初始状态"),
                ("normal-list|result-list|search-result", "结果列表类名"),
                ("title=\"|'title'", "标题字段"),
                ("\\d+\\s*条结果|共\\s*\\d+", "结果计数"),
                ("captcha|验证", "验证码")],
    "cqvip":   [("search-result|qikan-list|article-list", "结果列表"),
                ("403|412|blocked", "被拦")],
    "cnki":    [("kns8s|result-table|literature", "结果表格"),
                ("oversea", "海外入口")],
    "xmol":    [("paper-card|search-item|list-item", "结果卡片")],
    "baidu":   [("sc_default_result", "结果条目"),
                ("wappass|tuxing|captcha", "图形验证码")],
}


def run(name, url, extra_hdr, sess):
    hdr = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    hdr.update(extra_hdr)
    out = {"name": name, "url": url}
    t0 = time.time()
    try:
        r = sess.get(url, headers=hdr, timeout=25, allow_redirects=True)
        t = r.text or ""
        out.update({"code": r.status_code, "size": len(t), "final_url": r.url})
        # 线索
        hits = []
        for key, sigs in SIGNALS.items():
            if key in name:
                for rx, lbl in sigs:
                    if re.search(rx, t, re.I):
                        hits.append(lbl)
        out["signals"] = hits
        # 提取标题样本
        titles = re.findall(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>', t, re.S)
        titles += re.findall(r'"title"\s*:\s*"([^"]{8,120})"', t)
        clean = []
        for x in titles:
            x = re.sub(r"<[^>]+>", "", x).strip()
            x = re.sub(r"\s+", " ", x)
            if len(x) > 6:
                clean.append(x)
        out["sample_titles"] = clean[:6]
        out["title_count"] = len(clean)
        out["snippet"] = re.sub(r"\s+", " ", t[:500])
    except Exception as e:
        out.update({"code": 0, "error": f"{type(e).__name__}: {e}"[:200], "size": 0})
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def main():
    os.makedirs("out", exist_ok=True)
    sess = requests.Session()
    rows = []
    for name, url, hdr in TARGETS:
        row = run(name, url, hdr, sess)
        rows.append(row)
        print("[%-16s] code=%-4s size=%-8s titles=%-3s (%ss) %s" % (
            row["name"], row.get("code"), row.get("size"), row.get("title_count", "-"),
            row.get("elapsed"), ",".join(row.get("signals", []))[:40]), flush=True)
        time.sleep(2)
    json.dump({"rows": rows}, open("out/cn_probe.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n=== 详情 ===")
    for r in rows:
        print("\n--- %s (%s) ---" % (r["name"], r.get("code")))
        if r.get("sample_titles"):
            for t in r["sample_titles"][:3]:
                print("   ·", t[:90])
        elif r.get("snippet"):
            print("   ", r["snippet"][:180])
    print("\nCN_PROBE_DONE")


if __name__ == "__main__":
    main()
