#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_more_sources.py —— 再探一批「除了 Google 之外」的免费翻译源，做兜底梯队。

在 GitHub Actions runner 上跑。已确认可用：google-apis / google-com / mymemory。
本脚本只测「Google 之外的备份」，避免主力源一挂就全线停摆。

候选：
  edge-ms     微软 Edge 翻译（api-edge.cognitive.microsofttranslator.com，免 key，取 JWT）
  deepl-web   DeepL 网页端 jsonrpc（免 key，业界最强 EN→ZH 之一）
  papago      Naver Papago 网页 API
  youdao      有道 fanyi 网页端
  baidu       百度翻译 transapi
  argos-mirror LibreTranslate 第三方镜像（= 现网 LT 同款，仅作对照/末位）

只测不写：out/probe_more_sources.json + Step Summary。
"""
import json
import os
import random
import re
import time
import urllib.error
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
    ("severe duty", "These severe duty brake pads are for heavy trucks."),
    ("disc/pad/rotor", "Material developments are resulting in new disc, pad, "
     "and rotor designs."),
    ("占位符保持", "The brake pads wear over time. [IMG:1] The caliper squeezes "
     "the pads against the rotor."),
]

_IMG = re.compile(r"\[\s*IMG\s*:\s*(\d+)\s*\]")


def _read(req, timeout=20):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _post(url, data, headers, timeout=20):
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data,
                                 headers=dict({"User-Agent": UA}, **headers))
    return _read(req, timeout)


# ── 1) 微软 Edge 翻译（免 key，JWT 有效期约 10 分钟）────────────────
_EDGE_TOKEN = {"v": None, "t": 0}


def edge_token():
    if _EDGE_TOKEN["v"] and time.time() - _EDGE_TOKEN["t"] < 480:
        return _EDGE_TOKEN["v"]
    tok = _read(urllib.request.Request(
        "https://edge.microsoft.com/translate/auth",
        headers={"User-Agent": UA})).strip()
    _EDGE_TOKEN["v"], _EDGE_TOKEN["t"] = tok, time.time()
    return tok


def edge_ms(text):
    u = ("https://api-edge.cognitive.microsofttranslator.com/translate"
         "?api-version=3.0&from=en&to=zh-Hans")
    for _ in range(2):
        body = json.dumps([{"Text": text[:1000]}]).encode()
        req = urllib.request.Request(u, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + edge_token(),
            "User-Agent": UA})
        try:
            d = json.loads(_read(req))
            return d[0]["translations"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _EDGE_TOKEN["v"] = None   # token 过期，重取
                continue
            raise
    raise RuntimeError("edge token 反复 401")


# ── 2) DeepL 网页端 jsonrpc（免 key）────────────────────────────────
def deepl(text):
    ts = int(time.time() * 1000)
    icount = text.count("i")
    if icount:
        ts = ts - ts % (icount + 1) + (icount + 1)
    rid = random.randint(268435456, 4294967295)
    payload = {
        "jsonrpc": "2.0", "method": "LMT_handle_jobs",
        "params": {
            "jobs": [{"kind": "default", "raw_en_sentence": text[:4000],
                      "raw_en_context_before": [], "raw_en_context_after": [],
                      "preferred_num_beams": 4}],
            "lang": {"source_lang_user_selected": "EN",
                     "target_lang": "ZH"},
            "priority": 1, "commonJobParams": {}, "timestamp": ts,
        },
        "id": rid,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://www2.deepl.com/jsonrpc", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "DeepL/2241060 win10 Microsoft Edge/120",
                 "Referer": "https://www.deepl.com/translator",
                 "Accept": "*/*"})
    raw = _read(req)
    # DeepL 偶发返回带空字符的 JSON
    d = json.loads(raw.replace("\x00", ""))
    return d["result"]["translations"][0]["beams"][0]["postprocessed_sentence"]


# ── 3) Papago ──────────────────────────────────────────────────────
def papago(text):
    d = _post("https://papago.naver.com/apis/n2mt/translate",
              {"source": "en", "target": "zh-CN", "text": text[:1000]},
              {"Referer": "https://papago.naver.com/",
               "Content-Type": "application/x-www-form-urlencoded",
               "Device-Id": "probe", "Origin": "https://papago.naver.com"})
    return json.loads(d)["translatedText"]


# ── 4) 有道 ────────────────────────────────────────────────────────
def youdao(text):
    u = ("https://fanyi.youdao.com/translate?doctype=json&type=EN2ZH_CN&i=%s"
         % urllib.parse.quote(text[:1000]))
    d = json.loads(_read(urllib.request.Request(
        u, headers={"User-Agent": UA,
                    "Referer": "https://fanyi.youdao.com/"})))
    out = []
    for seg in d.get("translateResult", []):
        for x in seg:
            out.append(x.get("tgt", ""))
    return "".join(out)


# ── 5) 百度 transapi ───────────────────────────────────────────────
def baidu(text):
    u = ("https://fanyi.baidu.com/transapi?from=en&to=zh&query=%s"
         "&simple_means_flag=3" % urllib.parse.quote(text[:1000]))
    d = json.loads(_read(urllib.request.Request(
        u, headers={"User-Agent": UA,
                    "Referer": "https://fanyi.baidu.com/"})))
    return "".join(x.get("dst", "") for x in
                   (d.get("trans_result", {}).get("data") or []))


# ── 6) LibreTranslate 第三方镜像（对照/末位）───────────────────────
def argos_mirror(text):
    last = "no host"
    for host in ("https://libretranslate.de", "https://translate.astian.org",
                 "https://libretranslate.com"):
        try:
            body = json.dumps({"q": text[:1000], "source": "en",
                               "target": "zh", "format": "text"}).encode()
            d = json.loads(_post(host + "/translate", body,
                                 {"Content-Type": "application/json"}))
            return d["translatedText"]
        except Exception as e:
            last = "%s %s" % (host.split("//")[-1], str(e)[:40])
    raise RuntimeError(last)


SOURCES = [
    ("edge-ms", "微软 Edge 翻译（免 key JWT）", edge_ms),
    ("deepl-web", "DeepL 网页端 jsonrpc（免 key）", deepl),
    ("papago", "Naver Papago 网页 API", papago),
    ("youdao", "有道翻译网页端", youdao),
    ("baidu", "百度翻译 transapi", baidu),
    ("argos-mirror", "LibreTranslate 镜像（=现网 LT，对照）", argos_mirror),
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
                out = "<ERR %s>" % str(e)[:80]
            rec["samples"].append({"case": name, "en": text, "zh": out})
            print("  [%s/%s] %s" % (key, name, out[:100]))
        rec["seconds"] = round(time.time() - t0, 1)
        ph = next((x for x in rec["samples"] if x["case"] == "占位符保持"), None)
        rec["keep_ph"] = bool(ph and _IMG.search(ph["zh"]))
        print("\n### %s\n✅ 可用 %.1fs｜占位符 %s\n"
              % (label, rec["seconds"], "保持" if rec["keep_ph"] else "❌丢失"))
        result["sources"].append(rec)

    os.makedirs("out", exist_ok=True)
    json.dump(result, open("out/probe_more_sources.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write("## 备份翻译源探测（Google 之外）\n\n")
            f.write("| 源 | 状态 | 耗时 | [IMG:n] |\n|---|---|---|---|\n")
            for r in result["sources"]:
                f.write("| %s | %s | %s | %s |\n" % (
                    r["label"],
                    "✅ 可用" if r["ok"] else "❌ " + r["err"][:36],
                    r.get("seconds", "-"),
                    ("✅" if r.get("keep_ph") else "❌") if r["ok"] else "-"))
            f.write("\n### 各源译文对照\n\n")
            for r in result["sources"]:
                if not r["ok"]:
                    continue
                f.write("**%s**\n\n" % r["label"])
                for s in r["samples"]:
                    f.write("- `%s` → %s\n" % (s["case"], s["zh"][:120]))
                f.write("\n")


if __name__ == "__main__":
    main()
