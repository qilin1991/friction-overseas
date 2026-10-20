# -*- coding: utf-8 -*-
"""第二轮：把 Google Patents 的**字段结构**和**翻页参数**抓准（好写正式抓取器）。

第一轮已知：XHR 检索 200/JSON/13.5 万命中；详情页 200/522KB/无验证码；
但结果对象外层键是 `result` 不是 `patent`，且 assignee/inventor 的正则没命中。
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
OUT = "out/patent_probe2.json"
Q = "brake pad friction material composition"


def fetch(url, timeout=35, headers=None):
    h = {"User-Agent": UA, "Accept": "text/html,application/json,*/*",
         "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, ("ERR " + repr(e)).encode()


def xhr(q, extra="", extra_param=""):
    inner = "q=" + urllib.parse.quote_plus(q) + extra
    u = "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(inner, safe="")
    return u + extra_param


def walk(obj, prefix="", depth=0, out=None):
    """列出嵌套 dict 的键路径（最多 3 层）。"""
    if out is None:
        out = []
    if depth > 2:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = prefix + "." + k if prefix else k
            out.append(p + (" = " + str(v)[:60].replace("\n", " ")
                            if not isinstance(v, (dict, list)) else ""))
            walk(v, p, depth + 1, out)
    elif isinstance(obj, list) and obj:
        walk(obj[0], prefix + "[0]", depth + 1, out)
    return out


def main():
    os.makedirs("out", exist_ok=True)
    rep = {}

    # 1) XHR 结构
    st, b = fetch(xhr(Q), headers={"Accept": "application/json"})
    j = json.loads(b.decode("utf-8", "replace"))
    res = j.get("results") or {}
    clus = res.get("cluster") or []
    flat = []
    for c in clus:
        for r in (c if isinstance(c, list) else [c]):
            if isinstance(r, dict):
                flat.append(r)
    rep["total"] = res.get("total_num_results")
    rep["n_flat"] = len(flat)
    rep["result_keys"] = walk(flat[0])[:60] if flat else []
    rep["sample_json"] = json.dumps(flat[0], ensure_ascii=False)[:2500] if flat else ""

    # 2) 翻页参数试验：比较每种写法的第一条公开号
    def first_pn(u):
        try:
            s, bb = fetch(u, headers={"Accept": "application/json"})
            jj = json.loads(bb.decode("utf-8", "replace"))
            cc = (jj.get("results") or {}).get("cluster") or []
            for c in cc:
                for r in (c if isinstance(c, list) else [c]):
                    if isinstance(r, dict):
                        d = r.get("result") or r
                        d = d.get("patent") or d
                        for k in ("publication_number", "publicationNumber", "pn", "id"):
                            if isinstance(d.get(k), str):
                                return s, d[k]
            return s, ""
        except Exception as e:  # noqa: BLE001
            return 0, "ERR " + repr(e)[:60]

    rep["page_tests"] = {
        "base": first_pn(xhr(Q)),
        "q_page1": first_pn(xhr(Q, "&page=1")),
        "param_page1": first_pn(xhr(Q, "", "&page=1")),
        "param_num_start": first_pn(xhr(Q, "", "&num=10&start=10")),
        "q_num100": first_pn(xhr(Q, "&num=100")),
    }

    # 3) 详情页元数据结构（拿一个真实公开号）
    pn = ""
    s0, pn = first_pn(xhr(Q))
    rep["detail_pn"] = pn
    st2, b2 = fetch("https://patents.google.com/patent/%s/en" % pn if pn
                    else "https://patents.google.com/patent/US6080797A/en")
    txt = b2.decode("utf-8", "replace")
    rep["detail_status"] = st2
    rep["meta_tags"] = re.findall(
        r'<meta\s+name="([^"]{1,60})"\s+content="([^"]{0,160})"', txt)[:40]
    rep["schema_keys"] = list(set(re.findall(r'itemprop="([a-zA-Z]+)"', txt)))[:40]
    m = re.search(r'<section itemprop="description"[^>]*>([\s\S]{0,1200}?)</section>', txt)
    rep["abstract_html"] = re.sub(r"\s+", " ", m.group(1))[:700] if m else ""
    # 权利要求里出现重量百分比的片段（配方专利判定用）
    rep["claims_snippet"] = re.sub(r"\s+", " ", " ".join(
        re.findall(r'itemprop="claims"[^>]*>([\s\S]{0,600}?)</section>', txt))[:1])[:600]
    # 申请人/发明人：找 patent-bib 区域
    m2 = re.search(r'(Current Assignee|Assignee)[\s\S]{0,400}', txt)
    rep["assignee_ctx"] = re.sub(r"\s+", " ", m2.group(0))[:300] if m2 else ""

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print("PATENT_PROBE2_DONE total=%s pn=%s" % (rep["total"], pn), flush=True)


if __name__ == "__main__":
    main()
