# -*- coding: utf-8 -*-
"""探测 GitHub Actions（Azure 出口）上哪些**国外专利**通道可用。

背景：本机 + VPS 到 google.com / patents.google.com 直连与代理均 000/403，
Espacenet 有 Cloudflare；国内已有 CNIPA 通道，但**国外配方专利（US/EP/JP/WO/KR）**
一条都没有 —— 而 NAO 配方的主力专利恰恰在 Akebono / Nisshinbo / Advics /
TMD Friction / Federal-Mogul 这些日欧美厂商手里。

本脚本一次跑完，输出 out/patent_probe.json：
  A. Google Patents XHR 检索（patents.google.com/xhr/query）
  B. Google Patents 专利详情页（拿摘要/申请人/权利要求）
  C. Espacenet OPS（无 key 时的真实返回，用来判断要不要去注册）
  D. PatentsView search API（无 key 时的真实返回）
  E. WIPO Patentscope 结果页
  F. Espacenet 经典检索页（确认 Cloudflare 是否也拦 GH 出口）
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
OUT = "out/patent_probe.json"
QUERY = "brake pad friction material composition"


def fetch(url, timeout=35, headers=None, data=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/json,*/*",
         "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, ("ERR " + repr(e)).encode()


def xhr_url(q, extra=""):
    inner = "q=" + urllib.parse.quote_plus(q) + extra
    return "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(inner, safe="")


def probe_xhr():
    """A. Google Patents XHR 检索 + 翻页验证。"""
    out = {}
    st, b = fetch(xhr_url(QUERY), headers={"Accept": "application/json"})
    txt = b.decode("utf-8", "replace")
    out["status"] = st
    out["bytes"] = len(b)
    out["is_json"] = txt.lstrip().startswith(("{", "["))
    if not out["is_json"]:
        out["head"] = txt[:300]
        return out
    try:
        j = json.loads(txt)
    except Exception as e:  # noqa: BLE001
        out["json_err"] = repr(e)[:160]
        return out
    res = (j.get("results") or {})
    out["top_keys"] = list(j.keys())[:12]
    out["results_keys"] = list(res.keys())[:12]
    out["total_num_results"] = res.get("total_num_results")
    out["num_results"] = res.get("num_results")
    clus = res.get("cluster") or []
    out["clusters"] = len(clus)
    items = []
    for c in clus[:2]:
        for r in (c if isinstance(c, list) else [c])[:2]:
            if isinstance(r, dict):
                items.append(r)
    out["sample_keys"] = sorted(items[0].keys())[:20] if items else []
    out["sample"] = items[:2]
    # 翻页：q 里带 page=1 看第一条是否变化
    first = []
    for r in items[:1]:
        p = (r.get("patent") or {})
        first.append(p.get("publication_number"))
    st2, b2 = fetch(xhr_url(QUERY, "&page=1"), headers={"Accept": "application/json"})
    try:
        j2 = json.loads(b2.decode("utf-8", "replace"))
        clus2 = (j2.get("results") or {}).get("cluster") or []
        it2 = []
        for c in clus2[:1]:
            for r in (c if isinstance(c, list) else [c])[:1]:
                if isinstance(r, dict):
                    it2.append((r.get("patent") or {}).get("publication_number"))
        out["page1_status"] = st2
        out["page1_first"] = it2
        out["pagination_differs"] = bool(it2) and it2 != first
    except Exception as e:  # noqa: BLE001
        out["page1_err"] = repr(e)[:120]
    return out


def probe_detail(pnum):
    """B. 专利详情页（拿摘要/申请人）。"""
    out = {"patent": pnum}
    url = "https://patents.google.com/patent/%s/en" % pnum
    st, b = fetch(url)
    txt = b.decode("utf-8", "replace")
    out["status"] = st
    out["bytes"] = len(b)
    m = re.search(r'<meta name="description" content="([^"]{0,600})"', txt)
    out["meta_desc"] = (m.group(1)[:280] if m else "")
    m2 = re.search(r'itemprop="abstract".*?<section itemprop="description".*?</section>',
                   txt, re.S)
    out["has_abstract_block"] = bool(m2)
    # 申请人 / 发明人 / 日期
    out["assignee"] = (re.findall(r'itemprop="assignee"[^>]*>\s*<[^>]*>([^<]{2,60})', txt)
                       or [])[:3]
    out["inventor"] = (re.findall(r'itemprop="inventor"[^>]*>\s*<[^>]*>([^<]{2,40})', txt)
                       or [])[:3]
    out["dates"] = re.findall(r'<time itemprop="([a-zA-Z]+)" datetime="([^"]+)"', txt)[:4]
    out["captcha"] = any(k in txt for k in ("/sorry/", "unusual traffic", "not a robot"))
    return out


def probe_ops():
    """C. Espacenet OPS 无 key（判断注册必要性）。"""
    st, b = fetch("https://ops.epo.org/3.2/rest-services/published-data/search?q=ti%3Dbrake")
    return {"status": st, "bytes": len(b), "body": b.decode("utf-8", "replace")[:220]}


def probe_patentsview():
    """D. PatentsView search API 无 key。"""
    u = ("https://search.patentsview.org/api/v1/patent/?q="
         + urllib.parse.quote('{"_text_any":{"patent_title":"brake"}}'))
    st, b = fetch(u, headers={"Accept": "application/json"})
    return {"status": st, "bytes": len(b), "body": b.decode("utf-8", "replace")[:220]}


def probe_patentscope():
    """E. WIPO Patentscope 结果页。"""
    u = ("https://patentscope.wipo.int/search/en/result.jsf?query="
         + urllib.parse.quote("brake pad friction material"))
    st, b = fetch(u)
    txt = b.decode("utf-8", "replace")
    return {"status": st, "bytes": len(b),
            "has_result_table": ("resultListForm" in txt or "patentscope" in txt.lower()),
            "captcha": any(k in txt for k in ("captcha", "robot", "blocked"))}


def probe_espacenet():
    """F. Espacenet 经典检索页（Cloudflare 是否也拦 GH）。"""
    u = ("https://worldwide.espacenet.com/patent/search?q="
         + urllib.parse.quote("brake pad friction material"))
    st, b = fetch(u)
    txt = b.decode("utf-8", "replace")
    return {"status": st, "bytes": len(b),
            "cloudflare": any(k in txt for k in ("Just a moment", "cf-browser-verification",
                                                 "__cf_chl"))}


def main():
    os.makedirs("out", exist_ok=True)
    rep = {"query": QUERY}
    print("[A] Google Patents XHR …", flush=True)
    a = probe_xhr()
    rep["google_xhr"] = a
    print("    status=%s json=%s total=%s" % (
        a.get("status"), a.get("is_json"), a.get("total_num_results")), flush=True)

    pnum = ""
    try:
        pnum = (a["sample"][0].get("patent") or {}).get("publication_number") or ""
    except Exception:  # noqa: BLE001
        pass
    if pnum:
        print("[B] 详情页 %s …" % pnum, flush=True)
        rep["google_detail"] = probe_detail(pnum)
    else:
        print("[B] 详情页：无样本可试", flush=True)
        rep["google_detail"] = probe_detail("US6080797A")

    print("[C] Espacenet OPS（无 key）…", flush=True)
    rep["ops_nokey"] = probe_ops()
    print("[D] PatentsView（无 key）…", flush=True)
    rep["patentsview_nokey"] = probe_patentsview()
    print("[E] Patentscope …", flush=True)
    rep["patentscope"] = probe_patentscope()
    print("[F] Espacenet 经典页 …", flush=True)
    rep["espacenet"] = probe_espacenet()

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print("\nPATENT_PROBE_DONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
