# -*- coding: utf-8 -*-
"""probe_base.py —— 在 GitHub Actions（Azure 出口）探测 BASE API 真实行为。

要确认 4 件事：
  A) format=json 是否支持（否则要退回 XML）
  B) docs 字段名与类型（BASE 常用数组）
  C) 分页参数：offset= / start= / page=
  D) 结果是否含 doi / fulltext / abstract
"""
import json
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
API = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"
OUT = {}


def get(url, timeout=40):
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except Exception as e:  # noqa: BLE001
        return 0, "", ("ERR " + repr(e)).encode()


def q(extra=""):
    return API + "?func=PerformSearch&hits=10" + extra + \
        "&query=" + urllib.parse.quote_plus('"friction material" brake')


# A/B/D: JSON
st, ct, b = get(q("&format=json"))
OUT["json"] = {"status": st, "ctype": ct, "len": len(b),
               "head": b[:400].decode("utf-8", "replace")}
if st == 200:
    try:
        j = json.loads(b.decode("utf-8", "replace"))
        resp = j.get("response") or {}
        docs = resp.get("docs") or []
        OUT["json"]["numFound"] = resp.get("numFound")
        OUT["json"]["n_docs"] = len(docs)
        if docs:
            d = docs[0]
            OUT["json"]["doc_keys"] = list(d.keys())
            OUT["json"]["sample"] = {k: (v if isinstance(v, str) else str(v)[:200])
                                     for k, v in d.items()}
            OUT["json"]["first_title"] = str(d.get("title"))
    except Exception as e:  # noqa: BLE001
        OUT["json"]["parse_err"] = repr(e)

# 退回 XML 的形态
st2, ct2, b2 = get(q("&format=xml"))
OUT["xml"] = {"status": st2, "ctype": ct2, "len": len(b2),
              "head": b2[:300].decode("utf-8", "replace")}

# C: 分页
def first_id(extra):
    st, ct, b = get(q("&format=json" + extra))
    try:
        j = json.loads(b.decode("utf-8", "replace"))
        docs = ((j.get("response") or {}).get("docs") or [])
        if docs:
            d = docs[0]
            return str(d.get("identifier") or d.get("title"))[:90]
    except Exception:  # noqa: BLE001
        pass
    return ""


base_id = first_id("")
OUT["paging"] = {"offset0": base_id}
for tmpl, label in (("&offset=%d", "offset"), ("&start=%d", "start"), ("&page=%d", "page")):
    OUT["paging"][label + "10"] = first_id(tmpl % 10)

with open("out/base_probe.json", "w", encoding="utf-8") as fh:
    json.dump(OUT, fh, ensure_ascii=False, indent=1)

for k in ("json", "xml"):
    v = OUT[k]
    print("=== %s status=%s ctype=%s len=%s" % (k, v["status"], v["ctype"], v["len"]),
          flush=True)
    print("    head:", v["head"][:220].replace("\n", " "), flush=True)
print("numFound:", OUT["json"].get("numFound"), " n_docs:", OUT["json"].get("n_docs"),
      flush=True)
print("doc_keys:", OUT["json"].get("doc_keys"), flush=True)
print("sample:", json.dumps(OUT["json"].get("sample"), ensure_ascii=False)[:600], flush=True)
print("paging:", json.dumps(OUT["paging"], ensure_ascii=False), flush=True)
print("PROBE_BASE_DONE", flush=True)
