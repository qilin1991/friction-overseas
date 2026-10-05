#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_backup2.py —— 第二轮备份源探测。

第一轮结论（2026-10-05 16:01）：
  edge-ms 404 / deepl-web 429 / papago 404 / youdao 非JSON / baidu 空译文 /
  LibreTranslate 镜像 400 —— 全部不可用。

第二轮候选（仍未验证）：
  reverso    Reverso 网页 API（免 key，欧洲引擎）
  bing-v3    Bing ttranslatev3（先从 /translator 抓 IG + IID）
  lingva     Lingva 实例（Google 的开源代理，多个镜像）
  deepl-重试  第一轮是 429，多试几次看是不是瞬时限流
"""
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CASES = [
    ("caliper 卡滞", "If the caliper is stuck, remove the wheel and inspect "
     "the brake pad and rotor for wear."),
    ("washer/nut", "Fit a new sealing washer, then tighten the nut on the "
     "bleeder screw."),
    ("bleed 排气", "Bleed the brake system until fluid runs clear."),
    ("占位符保持", "The brake pads wear over time. [IMG:1] The caliper squeezes "
     "the pads against the rotor."),
]
_IMG = re.compile(r"\[\s*IMG\s*:\s*(\d+)\s*\]")


def _read(req, timeout=20):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def reverso(text):
    body = json.dumps({
        "from": "eng", "to": "chi", "input": text[:900], "format": "text",
        "options": {"sentenceSplitter": True, "origin": "reversomobile",
                    "contextResults": False, "languageDetection": False},
    }).encode()
    req = urllib.request.Request(
        "https://api.reverso.net/translate/v1/translation", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "Reverso/1.0", "Origin": "https://www.reverso.net"})
    d = json.loads(_read(req))
    return "".join(d.get("translation") or [])


_BING = {}


def bing_v3(text):
    if not _BING.get("IG"):
        html = _read(urllib.request.Request(
            "https://www.bing.com/translator", headers={"User-Agent": UA}), 25)
        m = re.search(r'IG:"([^"]+)"', html)
        iid = re.search(r'data-iid="([^"]+)"', html)
        if not m:
            raise RuntimeError("取不到 IG")
        _BING["IG"] = m.group(1)
        _BING["IID"] = iid.group(1) if iid else "translator.5023"
    u = ("https://www.bing.com/ttranslatev3?isVertical=1&&IG=%s&IID=%s"
         % (_BING["IG"], _BING["IID"]))
    data = urllib.parse.urlencode(
        {"fromLang": "en", "to": "zh-Hans", "text": text[:900]}).encode()
    req = urllib.request.Request(u, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": UA, "Referer": "https://www.bing.com/translator"})
    d = json.loads(_read(req))
    return d[0]["translations"][0]["text"]


LINGVA_HOSTS = ["https://lingva.ml", "https://translate.projectsegfau.lt",
                "https://lingva.garudalinux.org"]


def lingva(text):
    last = "no host"
    for h in LINGVA_HOSTS:
        try:
            u = "%s/api/v1/en/zh/%s" % (h, urllib.parse.quote(text[:800]))
            d = json.loads(_read(urllib.request.Request(
                u, headers={"User-Agent": UA})))
            return d["translation"]
        except Exception as e:
            last = "%s %s" % (h.split("//")[-1], str(e)[:40])
    raise RuntimeError(last)


def deepl_retry(text):
    last = ""
    for attempt in range(4):
        ts = int(time.time() * 1000)
        ic = text.count("i")
        if ic:
            ts = ts - ts % (ic + 1) + (ic + 1)
        payload = {
            "jsonrpc": "2.0", "method": "LMT_handle_jobs",
            "params": {"jobs": [{"kind": "default",
                                 "raw_en_sentence": text[:3000],
                                 "raw_en_context_before": [],
                                 "raw_en_context_after": [],
                                 "preferred_num_beams": 4}],
                       "lang": {"source_lang_user_selected": "EN",
                                "target_lang": "ZH"},
                       "priority": 1, "commonJobParams": {}, "timestamp": ts},
            "id": random.randint(268435456, 4294967295),
        }
        req = urllib.request.Request(
            "https://www2.deepl.com/jsonrpc", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "DeepL/2241060 win10 Microsoft Edge/120",
                     "Referer": "https://www.deepl.com/translator"})
        try:
            d = json.loads(_read(req).replace("\x00", ""))
            return d["result"]["translations"][0]["beams"][0][
                "postprocessed_sentence"]
        except Exception as e:
            last = str(e)[:60]
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(last)


SOURCES = [
    ("reverso", "Reverso 网页 API（免 key）", reverso),
    ("bing-v3", "Bing ttranslatev3（IG 令牌）", bing_v3),
    ("lingva", "Lingva 实例（Google 开源代理）", lingva),
    ("deepl-retry", "DeepL 网页端（重试 4 次）", deepl_retry),
]


def main():
    result = {"sources": []}
    for key, label, fn in SOURCES:
        rec = {"key": key, "label": label, "ok": False, "err": "", "samples": []}
        t0 = time.time()
        try:
            fn("Hello.")
            rec["ok"] = True
        except Exception as e:
            rec["err"] = str(e)[:120]
            print("\n### %s\n❌ %s" % (label, rec["err"]))
            result["sources"].append(rec)
            continue
        for name, text in CASES:
            try:
                out = fn(text)
            except Exception as e:
                out = "<ERR %s>" % str(e)[:70]
            rec["samples"].append({"case": name, "en": text, "zh": out})
            print("  [%s/%s] %s" % (key, name, out[:100]))
        rec["seconds"] = round(time.time() - t0, 1)
        ph = next((x for x in rec["samples"] if x["case"] == "占位符保持"), None)
        rec["keep_ph"] = bool(ph and _IMG.search(ph["zh"]))
        print("\n### %s\n✅ 可用 %.1fs｜占位符 %s\n"
              % (label, rec["seconds"], "保持" if rec["keep_ph"] else "❌丢失"))
        result["sources"].append(rec)

    os.makedirs("out", exist_ok=True)
    json.dump(result, open("out/probe_backup2.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write("## 第二轮备份源探测\n\n")
            f.write("| 源 | 状态 | 耗时 | [IMG:n] |\n|---|---|---|---|\n")
            for r in result["sources"]:
                f.write("| %s | %s | %s | %s |\n" % (
                    r["label"],
                    "✅" if r["ok"] else "❌ " + r["err"][:40],
                    r.get("seconds", "-"),
                    ("✅" if r.get("keep_ph") else "❌") if r["ok"] else "-"))
            for r in result["sources"]:
                if not r["ok"]:
                    continue
                f.write("\n**%s**\n\n" % r["label"])
                for s in r["samples"]:
                    f.write("- `%s` → %s\n" % (s["case"], s["zh"][:120]))


if __name__ == "__main__":
    main()
