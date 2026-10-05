#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_translate.py —— 在 GitHub Actions runner（海外 IP）上探测免费翻译源。

背景：VPS（腾讯云）出口访问 Google 全线不通（实测 Network unreachable），
      但 Actions runner 在 Azure 机房，translate.googleapis.com 本来就通
      —— 现有 collect_overseas.py 就是用它翻标题的。

目的：搞清楚哪些免费源在 runner 上可用、质量如何，为「正文多源翻译」选型。

⚠️ 历史坑：旧 Google 通道用 sl="auto" 自动检测源语言，把中文源当英文译，
   产出「评选的(CFSMA)」这类乱码。本探针一律强制 sl="en"。

只测不写，结果落到 out/probe_translate.json + Step Summary。
"""
import json
import os
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 对照组：全是摩擦/制动行业里 LibreTranslate 翻错过的句子
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


def google(tpl, text, timeout=20):
    u = tpl.format(q=urllib.parse.quote(text[:1800]))
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    out = []
    for seg in (d[0] if isinstance(d, list) and d else []):
        if seg and isinstance(seg, list) and seg[0]:
            out.append(seg[0])
    return "".join(out)


def mymemory(text, timeout=20):
    u = ("https://api.mymemory.translated.net/get?q=%s&langpair=en|zh-CN"
         % urllib.parse.quote(text[:500]))
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    return d.get("responseData", {}).get("translatedText", "")


def libre_public(text, timeout=25):
    for host in ("https://libretranslate.com", "https://translate.argosopentech.com"):
        try:
            body = json.dumps({"q": text[:1500], "source": "en",
                               "target": "zh", "format": "text"}).encode()
            req = urllib.request.Request(
                host + "/translate", data=body,
                headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())["translatedText"], host
        except Exception as e:
            last = str(e)[:60]
            continue
    raise RuntimeError(last)


SOURCES = [
    ("google-apis", "Google gtx @ translate.googleapis.com",
     lambda t: google("https://translate.googleapis.com/translate_a/single"
                      "?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}", t)),
    ("google-com", "Google gtx @ translate.google.com",
     lambda t: google("https://translate.google.com/translate_a/single"
                      "?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}", t)),
    ("mymemory", "MyMemory（免费额度，可带 email 提额）", mymemory),
    ("libre-public", "LibreTranslate 公共实例（= 现网 LT 同款，作对照）",
     lambda t: libre_public(t)[0]),
]


def main():
    egress = ""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            egress = r.read().decode()[:40]
    except Exception:
        pass

    print("出口 IP: %s" % (egress or "未知"))
    result = {"runner": {"egress_ip": egress}, "sources": []}

    for key, label, fn in SOURCES:
        rec = {"key": key, "label": label, "ok": False, "err": "", "samples": []}
        t0 = time.time()
        try:
            # 先探活
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
        print("\n### %s\n✅ 可用，%d 条样例，%.1fs\n" % (label, len(CASES), rec["seconds"]))
        result["sources"].append(rec)

    # 占位符保持性检查
    print("=== 占位符 [IMG:n] 保持检查 ===")
    for rec in result["sources"]:
        if not rec["ok"]:
            continue
        s = next((x for x in rec["samples"] if x["case"] == "占位符保持"), None)
        if s:
            kept = "[IMG:1]" in s["zh"]
            print("  %-14s %s" % (rec["key"], "✅ 保持" if kept else "❌ 丢失"))

    os.makedirs("out", exist_ok=True)
    with open("out/probe_translate.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Step Summary
    sum_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if sum_path:
        with open(sum_path, "a", encoding="utf-8") as f:
            f.write("## 免费翻译源探测（runner 出口 %s）\n\n" % (egress or "?"))
            f.write("| 源 | 状态 | 耗时 |\n|---|---|---|\n")
            for rec in result["sources"]:
                f.write("| %s | %s | %s |\n" % (
                    rec["label"], "✅ 可用" if rec["ok"] else "❌ " + rec["err"][:40],
                    rec.get("seconds", "-")))
            f.write("\n### 各源译文对照\n\n")
            for rec in result["sources"]:
                if not rec["ok"]:
                    continue
                f.write("**%s**\n\n" % rec["label"])
                for s in rec["samples"]:
                    f.write("- `%s` → %s\n" % (s["case"], s["zh"][:120]))
                f.write("\n")


if __name__ == "__main__":
    main()
