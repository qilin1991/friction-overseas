#!/usr/bin/env python3
"""translate_content.py — 用 Google 免费翻译端点（无 key、无 LLM）翻译正文。

输入: data/translate_queue.json  {"queue": [{url, title, text}, ...]}（VPS 推送）
输出: out/news_zh.json           {"zh": {url: {title_zh, content_zh}}}（提交回 data/）

设计要点：
- 走 translate.googleapis.com 免费端点（与 collect_overseas.py 标题翻译同源），
  但支持分块——collect_overseas 只翻 ≤480 字符的标题，正文必须切块。
- POST 表单提交（GET 的 URL 长度放不下 3200 字符的块）。
- 单条失败跳过不阻塞；全程无 API key、无 LLM，0 成本。
"""
import json
import os
import time
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) friction-actions/1.0"}
QUEUE_PATH = "data/translate_queue.json"
OUT_PATH = "out/news_zh.json"
CHUNK = 3200      # 单块字符上限
SLEEP = 0.35      # 块间限速


def gtranslate(text: str, retry: int = 3) -> str:
    """Google 免费翻译端点（POST）。失败返回空串。"""
    text = (text or "").strip()
    if not text:
        return ""
    for _ in range(retry + 1):
        try:
            r = requests.post(
                "https://translate.googleapis.com/translate_a/single",
                data={"client": "gtx", "sl": "auto", "tl": "zh-CN",
                      "dt": "t", "q": text},
                headers=UA, timeout=25)
            if r.status_code == 200:
                data = r.json()
                seg = "".join(p[0] for p in (data[0] or []) if p and p[0])
                return seg.strip()
        except Exception:
            pass
        time.sleep(1.2)
    return ""


def split_chunks(text: str, size: int = CHUNK) -> list:
    """按段落边界切块，超长段落硬切。"""
    paras = text.split("\n")
    buf, out = "", []
    for p in paras:
        if len(p) > size:
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(p), size):
                out.append(p[i:i + size])
            continue
        if len(buf) + len(p) + 1 > size:
            out.append(buf)
            buf = p
        else:
            buf = (buf + "\n" + p) if buf else p
    if buf:
        out.append(buf)
    return out


def main() -> int:
    if not os.path.exists(QUEUE_PATH):
        print("translate: 无待翻译队列，跳过")
        return 0
    with open(QUEUE_PATH, encoding="utf-8") as f:
        q = json.load(f).get("queue") or []
    print(f"translate: 待翻译 {len(q)} 条")
    zh, fail = {}, 0
    for i, item in enumerate(q, 1):
        url = item.get("url") or ""
        title = item.get("title") or ""
        text = item.get("text") or ""
        if not url or len(text) < 150:
            continue
        tzh = gtranslate(title) if title else ""
        parts, ok = [], True
        for ch in split_chunks(text):
            tr = gtranslate(ch)
            if not tr:
                ok = False
                break
            parts.append(tr)
            time.sleep(SLEEP)
        if not ok or not parts:
            fail += 1
            print(f"  [{i}/{len(q)}] FAIL 跳过: {url[:70]}")
            continue
        zh[url] = {"title_zh": tzh, "content_zh": "\n".join(parts)}
        print(f"  [{i}/{len(q)}] ok ({len(text)}字符/{len(parts)}块): {url[:60]}")
    os.makedirs("out", exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "friction-actions/translate_content.py",
        "count": len(zh),
        "zh": zh,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"translate: 完成 {len(zh)} 条，失败 {fail} → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
