# -*- coding: utf-8 -*-
"""probe_base2.py —— BASE 拒绝 Azure IP/UA，试多种 UA 判断是「UA 识别」还是「IP 封禁」。"""
import json
import urllib.parse
import urllib.request

API = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"
QS = "?func=PerformSearch&format=json&hits=3&query=" + urllib.parse.quote_plus(
    '"friction material" brake')

UAS = {
    "service_mailto": "FrictionLit/1.0 (https://example.org; mailto:qilin@example.com)",
    "service_plain": "FrictionLit/1.0",
    "python_default": "Python-urllib/3.11",
    "curl": "curl/8.0",
}
OUT = {}
for tag, ua in UAS.items():
    r = urllib.request.Request(API + QS, headers={"User-Agent": ua,
                                                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            b = resp.read()
            OUT[tag] = {"status": resp.status, "len": len(b),
                        "head": b[:320].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        OUT[tag] = {"err": repr(e)[:200]}

with open("out/base_probe2.json", "w", encoding="utf-8") as fh:
    json.dump(OUT, fh, ensure_ascii=False, indent=1)

for k, v in OUT.items():
    print("=== %-16s status=%s len=%s" % (k, v.get("status"), v.get("len")), flush=True)
    print("    ", (v.get("head") or v.get("err", ""))[:240].replace("\n", " "), flush=True)
print("PROBE_BASE2_DONE", flush=True)
