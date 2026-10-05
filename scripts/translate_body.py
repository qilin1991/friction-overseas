#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_body.py —— 在 GitHub Actions runner（海外 IP）上做正文多源免费翻译。

输入 data/translate_queue.json（VPS translate_queue.py --push 产出）
输出 data/news_zh.json（VPS translate_queue.py --merge 拉回回填）

多源策略（2026-10-05 探测结论，runner 出口 IP 172.x）：
  1) google-apis  translate.googleapis.com/translate_a/single?client=gtx  主力，4.8s/6条
  2) google-com   translate.google.com/translate_a/single?client=gtx      备用，0.5s/6条
  3) mymemory     api.mymemory.translated.net                             兜底，6.2s/6条
  ×) LibreTranslate 公共实例 —— runner 上 DNS 解析失败，不可用（本地 LT 同款，正是要替掉的）

质量实测（对照 LT 翻错过的句子）：
  LT          : caliper→卡路里  washer→洗衣机  nut→坚果  bleed→出血  disc→盘子
  google-apis : caliper→卡钳    washer→密封垫圈 nut→螺母  bleed→排气  disc→盘
  mymemory    : caliper→卡钳    washer→密封垫圈 nut→螺母  bleed→排出气液 disc→圆盘 pad→焊盘 ⚠

⚠️ 两个必须守住的坑：
  1) sl 一律强制 "en"。旧通道用 sl="auto"，把中文源当英文译，产出「评选的(CFSMA)」乱码。
  2) MyMemory 会把 [IMG:1] 变成 [IMG: 1]，必须后处理归一；且占位符数量必须对齐，
     否则判定该源失败并换源（宁可留英文，也不能让图片占位符丢掉）。
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
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

QUEUE = "data/translate_queue.json"
OUT = "data/news_zh.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
ASCII = re.compile(r"[A-Za-z]")
IMG_PH = re.compile(r"\[\s*IMG\s*:\s*(\d+)\s*\]")       # 归一用：[IMG: 1] → [IMG:1]
IMG_ANY = re.compile(r"\[[^\[\]]{0,20}IMG[^\[\]]{0,20}\]")  # 任何变形都算占位符

SLEEP = 0.22          # 每请求间隔，防 429
MAX_CHARS = 1600      # 单请求字符上限（gtx URL 长度安全线）
RETRY = 3


# ── 各翻译源 ────────────────────────────────────────────────────────
def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def google(host, text):
    """gtx 端点。⚠ sl 强制 en —— 不要改成 auto。"""
    u = ("https://%s/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q=%s"
         % (host, urllib.parse.quote(text[:MAX_CHARS])))
    d = json.loads(_get(u))
    out = []
    for seg in (d[0] if isinstance(d, list) and d else []):
        if seg and isinstance(seg, list) and seg[0]:
            out.append(seg[0])
    return "".join(out)


def mymemory(text, email=""):
    u = ("https://api.mymemory.translated.net/get?q=%s&langpair=en%%7Czh-CN%s"
         % (urllib.parse.quote(text[:480]),
            ("&de=" + urllib.parse.quote(email)) if email else ""))
    d = json.loads(_get(u))
    return d.get("responseData", {}).get("translatedText", "")


def make_sources(email=""):
    return [
        ("google-apis", lambda t: google("translate.googleapis.com", t)),
        ("google-com", lambda t: google("translate.google.com", t)),
        ("mymemory", lambda t: mymemory(t, email)),
    ]


# ── 文本处理 ────────────────────────────────────────────────────────
def is_zh(s):
    """已是中文的段（含中文标题/导语混合体）直接放行，不送翻译。"""
    s = s.strip()
    if not s:
        return True
    return len(CJK.findall(s)) / max(1, len(s)) > 0.25


def split_paras(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def norm_ph(s):
    """把 [IMG: 1] / ［IMG：1］ 之类变形统一回 [IMG:1]。"""
    return IMG_PH.sub(lambda m: "[IMG:%s]" % m.group(1), s)


def call_with_retry(fn, text, stats, key):
    """带退避重试；429/5xx 换源由调用方处理。"""
    for i in range(RETRY):
        try:
            return fn(text)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 + 4 * i)
                continue
            raise
        except Exception:
            time.sleep(1 + 2 * i)
    raise RuntimeError("retry exhausted")


def translate_paragraph(text, sources, stats, order=None):
    """逐源尝试，返回 (译文, 使用的源)。全失败返回 (None, None)。"""
    ph_in = len(IMG_ANY.findall(text))
    for key, fn in (sources if order is None else [s for s in sources if s[0] in order]):
        try:
            out = call_with_retry(fn, text, stats, key)
        except Exception as e:
            stats[key + "_err"] = stats.get(key + "_err", 0) + 1
            last = str(e)[:80]
            continue
        if not out or not out.strip():
            stats[key + "_empty"] = stats.get(key + "_empty", 0) + 1
            continue
        out = norm_ph(out.strip())
        # 门禁 1：占位符数量必须对齐
        if len(IMG_ANY.findall(out)) != ph_in:
            stats[key + "_phlost"] = stats.get(key + "_phlost", 0) + 1
            continue
        # 门禁 2：原样返回 = 没翻（源语言判错/被拒）
        if out == text.strip() and len(ASCII.findall(text)) > 20:
            stats[key + "_noop"] = stats.get(key + "_noop", 0) + 1
            continue
        # 门禁 3：长度塌缩（疑似摘要/截断）
        if len(out) < max(4, len(text) * 0.10):
            stats[key + "_short"] = stats.get(key + "_short", 0) + 1
            continue
        stats[key] = stats.get(key, 0) + 1
        time.sleep(SLEEP)
        return out, key
    return None, None


def translate_text(text, sources, stats):
    paras = split_paras(text)
    out, used = [], {}
    for p in paras:
        body = p.strip()
        if is_zh(body) or len(ASCII.findall(body)) < 3:
            out.append(body)
            continue
        # 超长段按句切，逐片翻
        if len(body) > MAX_CHARS:
            pieces = re.split(r"(?<=[.!?。！？])\s+", body)
            buf, chunks, cur = [], [], ""
            for s in pieces:
                if len(cur) + len(s) + 1 > MAX_CHARS and cur:
                    chunks.append(cur)
                    cur = s
                else:
                    cur = (cur + " " + s).strip()
            if cur:
                chunks.append(cur)
            got = []
            for ch in chunks:
                r, k = translate_paragraph(ch, sources, stats)
                got.append(r if r else ch)
                used[k] = used.get(k, 0) + 1
            out.append("".join(got))
            continue
        r, k = translate_paragraph(body, sources, stats)
        if r is None:
            # 全源失败：保留英文原段，让 zh_guard 在 VPS 侧发现
            out.append(body)
            used["FAILED"] = used.get("FAILED", 0) + 1
        else:
            out.append(r)
            used[k] = used.get(k, 0) + 1
    return "\n\n".join(out), paras, used


def main():
    raw_limit = (sys.argv[1] if len(sys.argv) > 1
                 else os.environ.get("T_LIMIT") or "0")
    limit = int(raw_limit or 0)
    email = os.environ.get("MYMEMORY_EMAIL", "")
    if not os.path.exists(QUEUE):
        print("无 %s，退出" % QUEUE)
        return
    q = json.load(open(QUEUE, encoding="utf-8"))
    items = q.get("queue", [])
    if limit:
        items = items[:limit]
    print("待翻 %d 条（队列共 %d）" % (len(items), q.get("count", 0)))

    sources = make_sources(email)
    # 先探活：主力源挂了就别浪费时间
    live = []
    for key, fn in sources:
        try:
            fn("Hello.")
            live.append((key, fn))
            print("  ✅ %s 可用" % key)
        except Exception as e:
            print("  ❌ %s 不可用: %s" % (key, str(e)[:60]))
    if not live:
        print("❌ 全部翻译源不可用，不写结果（保留上次产物）")
        return
    sources = live

    # 读旧产物，增量合并（绝不整份覆盖，避免丢掉历史译文）
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("zh", {}) or {}
        except Exception:
            old = {}
    print("已有历史译文 %d 条" % len(old))

    stats, done, t0 = {}, 0, time.time()
    for i, it in enumerate(items, 1):
        url, title, text = it.get("url"), it.get("title") or "", it.get("text") or ""
        if not url or len(text.strip()) < 40:
            continue
        czh, paras, used = translate_text(text, sources, stats)
        # 结构门禁：段落数必须一致
        if len(czh.split("\n\n")) != len(paras):
            stats["PARA_MISMATCH"] = stats.get("PARA_MISMATCH", 0) + 1
            print("  ⚠ %s 段落数不符，跳过本条" % url[:70])
            continue
        tzh = title
        if title and not is_zh(title) and len(ASCII.findall(title)) > 3:
            r, k = translate_paragraph(title, sources, stats)
            if r:
                tzh = r
        old[url] = {"title_zh": tzh, "content_zh": czh}
        done += 1
        en_len, zh_len = len(text), len(czh)
        print("  [%d/%d] %s | %d→%d chars (%.2f) | %s"
              % (i, len(items), url[:58], en_len, zh_len,
                 zh_len / max(1, en_len), ",".join(sorted(used))))

    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "generator": "friction-actions/translate_body.py",
               "count": len(old), "done_this_run": done,
               "seconds": round(time.time() - t0, 1),
               "stats": stats, "zh": old}
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n✅ 写出 %s：累计 %d 条，本轮 %d 条，%.0fs"
          % (OUT, len(old), done, time.time() - t0))
    print("   分源计数: %s" % stats)

    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write("## 正文翻译（多源）\n\n")
            f.write("- 本轮完成：**%d** 条，累计产物 **%d** 条，用时 %.0fs\n"
                    % (done, len(old), time.time() - t0))
            f.write("- 分源命中：%s\n" % ", ".join(
                "%s=%d" % (k, v) for k, v in sorted(stats.items())
                if not k.endswith(("_err", "_empty", "_phlost", "_noop",
                                   "_short"))))
            bad = {k: v for k, v in stats.items()
                   if k.endswith(("_err", "_empty", "_phlost", "_noop",
                                  "_short", "MISMATCH"))}
            if bad:
                f.write("- ⚠️ 异常计数：%s\n" % ", ".join(
                    "%s=%d" % (k, v) for k, v in sorted(bad.items())))


if __name__ == "__main__":
    main()
