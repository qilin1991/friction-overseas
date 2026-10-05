#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_body.py —— 在 GitHub Actions runner（海外 IP）上做正文多源免费翻译。

输入 data/translate_queue.json（VPS translate_queue.py --push 产出）
输出 data/news_zh.json（VPS translate_queue.py --merge 拉回回填）
缓存 data/translate_cache.json（跨轮复用，锅炉段/重复段不再烧请求）

多源策略（2026-10-05 探测结论，runner 出口 172.x）：
  1) google-apis  translate.googleapis.com/translate_a/single?client=gtx  主力
  2) google-com   translate.google.com/translate_a/single?client=gtx      备用（另一主机，429 时立刻切）
  3) mymemory     api.mymemory.translated.net                             末位兜底
  ×) LibreTranslate 公共实例 —— runner 上 DNS 解析失败，不可用（本地 LT 同款，正是要替掉的）

质量实测（对照 LT 翻错过的句子）：
  LT          : caliper→卡路里  washer→洗衣机  nut→坚果  bleed→出血  disc→盘子
  google-apis : caliper→卡钳    washer→密封垫圈 nut→螺母  bleed→排气  disc→盘
  mymemory    : caliper→卡钳    washer→密封垫圈 nut→螺母  disc→圆盘 pad→焊盘 ⚠

⚠️ 三个必须守住的坑：
  1) sl 绝不设 "auto"。中文源会被当英文译，产出「评选的(CFSMA)」乱码。
     VPS 侧只推 lang='en' 的文章；这里再按停用词做一次非英语语种判定（es/fr/de/it/pt）
     并显式指定 sl，其余一律 sl="en"。
  2) [IMG:n] 占位符绝不能丢：整段翻优先，占位符数量对不上就把段落按占位符切开、
     逐片翻译再原样插回（2026-10-05 实测：不这么做会有 22 段整段回退成英文）。
  3) 别在 429 上死等：一次失败立刻换主机，换源；重试退避累计起来会把 4 分钟的活拖到 25 分钟+。
"""
import hashlib
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
CACHE = "data/translate_cache.json"
DONE = "data/translate_done.json"   # VPS 回填成功后回传的清单，用来裁剪 OUT

CJK = re.compile(r"[\u4e00-\u9fff]")
ASCII = re.compile(r"[A-Za-z]")
IMG_ANY = re.compile(r"[\[［【][^\[\]］】]{0,20}IMG[^\[\]］】]{0,20}[\]］】]")
IMG_PH = re.compile(r"\[\s*IMG\s*:\s*(\d+)\s*\]")

SLEEP = 0.15            # 正常节奏下的请求间隔
MAX_CHARS = 1500        # 单请求字符上限
BATCH_CHARS = 1200      # 多段合并上限
MAX_SECONDS = int(os.environ.get("T_MAX_SECONDS", "1500"))  # 硬超时，到点就写入已有成果
CACHE_MAX = 4000        # 缓存条目上限（约 700KB，别把仓库撑爆）

# 锅炉段 / 非正文：直接放行，不烧请求
JUNK = re.compile(
    r"^\s*(https?://\S+|www\.\S+|\S+@\S+\.\S+|©.*|all rights reserved|"
    r"privacy policy|terms of (use|service)|cookie[s]? policy|subscribe|"
    r"sign up|follow us|share this|related (articles?|posts?|news)|"
    r"read more|tags?\s*:|source\s*:|photo\s*:|image credit|getty images|"
    r"advertisement|sponsored|newsletter|media contact|about the author)\s*$",
    re.I)

STOP = {
    "en": {"the", "and", "of", "to", "is", "for", "in", "that", "with",
           "on", "as", "are", "was", "by", "this", "from", "at", "an",
           "it", "be", "has", "will", "its"},
    "es": {"de", "la", "el", "los", "las", "y", "en", "para", "con", "que",
           "del", "un", "una", "es", "por", "se", "su", "al", "como",
           "este", "más", "no", "lo"},
    "fr": {"de", "la", "le", "les", "et", "en", "pour", "avec", "que",
           "du", "un", "une", "est", "par", "se", "son", "au", "comme",
           "plus", "cette", "des", "dans"},
    "de": {"der", "die", "das", "und", "in", "für", "mit", "dass", "von",
           "ein", "eine", "ist", "auf", "sich", "zu", "als", "auch",
           "werden", "nicht", "im", "den"},
    "it": {"di", "la", "il", "le", "e", "in", "per", "con", "che", "del",
           "un", "una", "si", "suo", "al", "come", "più", "questo", "nel"},
    "pt": {"de", "a", "o", "as", "os", "e", "em", "para", "com", "que",
           "do", "um", "uma", "por", "se", "seu", "ao", "como", "mais",
           "no", "da"},
}


# ── 翻译源 ──────────────────────────────────────────────────────────
def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def google(host, text, sl):
    """⚠ sl 显式指定，禁止 auto。"""
    u = ("https://%s/translate_a/single?client=gtx&sl=%s&tl=zh-CN&dt=t&q=%s"
         % (host, sl, urllib.parse.quote(text[:MAX_CHARS])))
    d = json.loads(_get(u))
    out = []
    for seg in (d[0] if isinstance(d, list) and d else []):
        if seg and isinstance(seg, list) and seg[0]:
            out.append(seg[0])
    return "".join(out)


def mymemory(text, email, sl):
    u = ("https://api.mymemory.translated.net/get?q=%s&langpair=%s%%7Czh-CN%s"
         % (urllib.parse.quote(text[:480]), sl,
            ("&de=" + urllib.parse.quote(email)) if email else ""))
    d = json.loads(_get(u))
    return d.get("responseData", {}).get("translatedText", "")


def make_sources(email=""):
    """梯队：Google 系多域名 → MyMemory。
    2026-10-05 实测 runner 上可用：googleapis / google.com / mymemory。
    其余（Edge / DeepL / Papago / 有道 / 百度 / Reverso / Lingva / LT 镜像）
    全部不可用：404、429、空译文或原样返回。故不纳入。"""
    src = [(k, (lambda h: (lambda t, sl: google(h, t, sl)))(h))
           for k, h in (
               ("google-apis", "translate.googleapis.com"),
               ("google-com", "translate.google.com"),
               ("google-hk", "translate.google.com.hk"),
               ("google-jp", "translate.google.co.jp"),
           )]
    src.append(("mymemory", lambda t, sl: mymemory(t, email, sl)))
    return src


# ── 文本判定 ────────────────────────────────────────────────────────
def is_zh(s):
    s = s.strip()
    if not s:
        return True
    return len(CJK.findall(s)) / max(1, len(s)) > 0.25


def detect_sl(s):
    """按停用词判源语言。默认 en（VPS 只推 lang='en' 的文章）。"""
    words = re.findall(r"[a-zA-ZÀ-ÿ]+", s.lower())
    if len(words) < 6:
        return "en"
    best, bs = "en", 0.0
    for lang, sw in STOP.items():
        hit = sum(1 for w in words if w in sw)
        sc = hit / len(words)
        if sc > bs:
            best, bs = lang, sc
    return best if bs >= 0.15 else "en"


def split_paras(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def norm_ph(s):
    return IMG_PH.sub(lambda m: "[IMG:%s]" % m.group(1), s)


def skip_para(s):
    """锅炉段 / 已是中文 / 无实质英文 → 原样放行。"""
    t = s.strip()
    if not t or is_zh(t):
        return True
    if JUNK.match(t):
        return True
    if len(ASCII.findall(t)) < 6:      # 纯数字/符号/超短
        return True
    if re.match(r"^[\W\d_]+$", t):
        return True
    return False


# ── 核心调用 ────────────────────────────────────────────────────────
def _split_long(text):
    """按句切成 ≤MAX_CHARS 的片，绝不从中间硬剁。"""
    pieces = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks, cur = [], ""
    for s in pieces:
        if len(cur) + len(s) + 1 > MAX_CHARS and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks or [text[:MAX_CHARS]]


def translate_plain(text, sources, stats, cache):
    """纯文本（无占位符）逐源尝试一次即换，返回 (译文, 源)。全失败 (None, None)。"""
    sl = detect_sl(text)
    ck = hashlib.sha1((sl + "::" + text).encode("utf-8")).hexdigest()
    if ck in cache:
        stats["CACHE"] = stats.get("CACHE", 0) + 1
        return cache[ck], "cache"

    # 超长段：按句切成 ≤MAX_CHARS 的片，逐片翻后拼回（否则会被 gtx 截断）
    if len(text) > MAX_CHARS:
        outs = []
        for ch in _split_long(text):
            r, _ = translate_plain(ch, sources, stats, cache)
            outs.append(r if r else ch)
        res = "".join(outs)
        if len(cache) < CACHE_MAX:
            cache[ck] = res
        return res, "chunked"

    for key, fn in sources:
        if stats.get("_" + key + "_dead_until", 0) > time.time():
            continue
        try:
            out = fn(text, sl)
        except urllib.error.HTTPError as e:
            stats[key + "_http" + str(e.code)] = \
                stats.get(key + "_http" + str(e.code), 0) + 1
            if e.code == 429:      # 别死等，标记该源冷却 60s 后换下一个
                stats["_" + key + "_dead_until"] = time.time() + 60
            continue
        except Exception:
            stats[key + "_err"] = stats.get(key + "_err", 0) + 1
            continue
        if not out or not out.strip():
            stats[key + "_empty"] = stats.get(key + "_empty", 0) + 1
            continue
        out = norm_ph(out.strip())
        if out == text.strip() and len(ASCII.findall(text)) > 20:
            stats[key + "_noop"] = stats.get(key + "_noop", 0) + 1
            continue
        if len(out) < max(4, len(text) * 0.10):
            stats[key + "_short"] = stats.get(key + "_short", 0) + 1
            continue
        if len(cache) < CACHE_MAX:
            cache[ck] = out
        stats[key] = stats.get(key, 0) + 1
        time.sleep(SLEEP)
        return out, key
    return None, None


def translate_para(text, sources, stats, cache):
    """单段：整段翻优先（上下文最完整）；占位符丢了就切开兜底。"""
    phs = list(IMG_ANY.finditer(text))
    if not phs:
        return translate_plain(text, sources, stats, cache)

    r, k = translate_plain(text, sources, stats, cache)
    if r is not None and len(IMG_ANY.findall(r)) == len(phs):
        return r, k
    if r is not None:
        stats[(k or "?") + "_phlost"] = stats.get((k or "?") + "_phlost", 0) + 1

    pieces = IMG_ANY.split(text)      # 以占位符为界切开
    out = []
    for i, seg in enumerate(pieces):
        s = seg.strip()
        if s and not skip_para(s):
            rr, _ = translate_plain(s, sources, stats, cache)
            out.append(rr if rr else s)
        else:
            out.append(seg)
        if i < len(phs):
            out.append(phs[i].group(0))
    stats["SEGSPLIT"] = stats.get("SEGSPLIT", 0) + 1
    return "".join(out), "segmented"


def translate_batch(paras, sources, stats, cache):
    """把连续的无占位符段落合并成一个请求（\n 连接），行对齐就用，否则逐段翻。"""
    out, i = [], 0
    while i < len(paras):
        buf, n = [], 0
        while i < len(paras) and (not buf or n + len(paras[i]) <= BATCH_CHARS):
            buf.append(paras[i])
            n += len(paras[i]) + 1
            i += 1
        joined = "\n".join(buf)
        if len(buf) > 1:
            r, k = translate_plain(joined, sources, stats, cache)
            lines = (r or "").split("\n")
            if r and len(lines) == len(buf) and all(x.strip() for x in lines):
                stats["BATCH"] = stats.get("BATCH", 0) + 1
                out.extend(x.strip() for x in lines)
                continue
        for p in buf:
            rr, _ = translate_plain(p, sources, stats, cache)
            out.append(rr if rr else p)
    return out


def translate_text(text, sources, stats, cache):
    paras = split_paras(text)
    plain_idx, plain_txt = [], []
    for i, p in enumerate(paras):
        if not skip_para(p) and not IMG_ANY.search(p):
            plain_idx.append(i)
            plain_txt.append(p.strip())

    got = translate_batch(plain_txt, sources, stats, cache)
    cache_map = dict(zip(plain_idx, got))

    out, used, left_en = [], {}, 0
    for i, p in enumerate(paras):
        if i in cache_map:
            out.append(cache_map[i])
            used["ok"] = used.get("ok", 0) + 1
            continue
        if skip_para(p):
            out.append(p.strip())
            continue
        r, k = translate_para(p.strip(), sources, stats, cache)
        if r is None:
            out.append(p.strip())
            left_en += 1
            used["FAILED"] = used.get("FAILED", 0) + 1
        else:
            out.append(r)
            used[k] = used.get(k, 0) + 1
    return "\n\n".join(out), paras, used, left_en


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

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    print("缓存 %d 条" % len(cache))

    sources = make_sources(email)
    live = []
    for key, fn in sources:
        try:
            fn("Hello.", "en")
            live.append((key, fn))
            print("  ✅ %s 可用" % key)
        except Exception as e:
            print("  ❌ %s 不可用: %s" % (key, str(e)[:60]))
    if not live:
        print("❌ 全部翻译源不可用，不写结果（保留上次产物）")
        return
    sources = live

    # VPS 已回填的清单：从产物里裁掉，防止 news_zh.json 越滚越大
    # （2026-10-06 踩过：超过 1MB 后 Contents API 不再内联返回 content，VPS 拉不回去）
    done = set()
    if os.path.exists(DONE):
        try:
            done = set(json.load(open(DONE, encoding="utf-8")).get("urls", []))
        except Exception:
            done = set()
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("zh", {}) or {}
        except Exception:
            old = {}
    pruned = len(old) - len([k for k in old if k not in done])
    old = {k: v for k, v in old.items() if k not in done}
    print("已回填清单 %d 条｜产物裁剪掉 %d 条，保留待回填 %d 条"
          % (len(done), pruned, len(old)))

    stats, done, left_total, t0 = {}, 0, 0, time.time()
    for i, it in enumerate(items, 1):
        if time.time() - t0 > MAX_SECONDS:
            print("⏱ 到达 %ds 硬超时，停止并写入已完成部分" % MAX_SECONDS)
            stats["TIMEOUT_STOP"] = len(items) - i + 1
            break
        url, title, text = it.get("url"), it.get("title") or "", it.get("text") or ""
        if not url or len(text.strip()) < 40:
            continue
        czh, paras, used, left_en = translate_text(text, sources, stats, cache)
        left_total += left_en
        if len(czh.split("\n\n")) != len(paras):
            stats["PARA_MISMATCH"] = stats.get("PARA_MISMATCH", 0) + 1
            print("  ⚠ %s 段落数不符，跳过本条" % url[:70])
            continue
        tzh = title
        if title and not is_zh(title) and len(ASCII.findall(title)) > 3:
            r, k = translate_plain(title, sources, stats, cache)
            if r:
                tzh = r
        old[url] = {"title_zh": tzh, "content_zh": czh}
        done += 1
        print("  [%d/%d] %s | %d→%d (%.2f) | 残留英文段 %d | %s"
              % (i, len(items), url[:52], len(text), len(czh),
                 len(czh) / max(1, len(text)), left_en,
                 ",".join(sorted(used))))

    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "generator": "friction-actions/translate_body.py",
               "count": len(old), "done_this_run": done,
               "seconds": round(time.time() - t0, 1),
               "stats": stats, "zh": old}
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n✅ 写出 %s：累计 %d 条，本轮 %d 条，%.0fs，残留英文段 %d"
          % (OUT, len(old), done, time.time() - t0, left_total))
    print("   分源: %s" % {k: v for k, v in sorted(stats.items())
                           if not k.startswith("_")})

    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write("## 正文翻译（多源）\n\n")
            f.write("- 本轮 **%d** 条，累计 **%d** 条，用时 %.0fs，残留英文段 %d\n"
                    % (done, len(old), time.time() - t0, left_total))
            f.write("- 请求分布：%s\n" % ", ".join(
                "%s=%d" % (k, v) for k, v in sorted(stats.items())
                if not k.startswith("_")))


if __name__ == "__main__":
    main()
