#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海外源正文 + 图片/视频补抓（跑在 GitHub Actions）。

背景：VPS（腾讯云）出口 IP 被 Cloudflare 企业级挑战拦截，
      import-car.com / fleetowner.com / bosch-presse.de 等站点在 VPS 上
      只能拿到 403 的 "Just a moment..." 挑战页，既没正文也没图。
      GitHub Actions runner 在 Azure 海外机房，出口 IP 与 VPS 不同，用它抓。

输入：data/media_queue.json（或 out/media_urls.txt）
      [{"url": ..., "id": 123}, ...]  id 为可选的 VPS 侧 article.id（仅作日志）

输出：out/news_media.json
      {"url": {"text": "...", "images": [{"url","caption"}], "videos": [...]}}
      workflow 提交到 data/news_media.json，
      VPS 侧 pull_media.py 读它 -> 写 article.raw_content / article_media 表。

特性：
- 直连优先；被 Cloudflare 拦时回落 r.jina.ai 阅读器（Actions 可达 jina）
- <img> 过滤：去 logo/avatar/icon/spacer/1x1/追踪像素/base64 占位
- <video>/<source>/<iframe>（YouTube/Vimeo）都收
- 绝对化 protocol-relative URL（//cdn.x.com/a.jpg）
- 增量：data/news_media.json 里已有且图片数 >0 的 url 不重抓
"""
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
JINA = "https://r.jina.ai/"
LIMIT = int(os.environ.get("MEDIA_LIMIT", "60"))
# jina 兜底开关：默认开；若日志显示 jina 也被上游拦（同 403），设 USE_JINA=0 省时间
USE_JINA = os.environ.get("USE_JINA", "1") not in ("0", "false", "False")

# 图片黑名单：文件名命中即丢
JUNK_IMG = re.compile(
    r"(logo|avatar|icon|spacer|blank|pixel|beacon|badge|banner-ad|advert|"
    r"social|share|favicon|sprite|placeholder|1x1|transparent|"
    r"emoji|gravatar|profile|thumb_?blank|default[-_]img|"
    # 站点品牌装饰图（实测 bosch/AMN 这类站把 logo 塞进正文流）
    r"monogram|invented-for-life|babcox-bug|"
    # Google News / 订阅 CTA 按钮
    r"preferred-source|follow-google|newsletter|subscribe[-_]?(btn|button)|"
    r"google-preferred|follow-us|"
    # 站点页眉页脚装饰
    r"header|footer|masthead|wordmark|brandmark)", re.I)
# 缩略图尺寸后缀：Bosch 这类 DAM 会给正文图加 _img_w386 / _img_w640，
# 只拿到小图；保留原图（去掉后缀）更好，但先只过滤纯缩略尺寸
THUMB_SUFFIX = re.compile(r"_img_w\d{2,4}\.(jpe?g|png|gif|webp)$", re.I)
IMG_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|bmp)(?:$|\?)", re.I)
# 视频：YouTube / Vimeo / Dailymotion 的 iframe
VID_HOST = re.compile(r"(youtube\.com|youtu\.be|vimeo\.com|dailymotion\.com|"
                      r"wistia\.(com|net)|brightcove|jwplayer|streamable)", re.I)
CF_MARKERS = ("just a moment", "cf-browser-verification", "cf_chl_opt",
              "checking your browser", "enable javascript and cookies")
# 正文容器优先级
CONTENT_SEL = [
    "article", "div.article-body", "div.article__body", "div.entry-content",
    "div.post-content", "div.story-content", "div#article-body",
    "div.td-post-content", "div.article-content", "main", "div.content",
]
DROP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form",
             "noscript", "iframe", "svg", "button", "figure"]


def _abs(u, base):
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("data:"):
        return ""
    return urljoin(base, u)


def _clean_jina(text):
    """剥掉 jina 阅读器首部元数据行。"""
    meta = ("title:", "published:", "url:", "image:", "description:",
            "total tokens:", "warning:", "markdown content:")
    out, in_meta = [], True
    for ln in text.splitlines():
        s = ln.strip()
        if in_meta:
            low = s.lower()
            if any(low.startswith(p) for p in meta):
                continue
            if s.startswith("http://") or s.startswith("https://"):
                continue
            if (not s or len(s) < 30) and not out:
                continue
            in_meta = False
        out.append(ln)
    return "\n".join(out).strip()


def extract_media(soup, base):
    """从 HTML 里提取图片/视频，返回 (images, videos)。"""
    images, videos = [], []
    seen_i, seen_v = set(), set()

    # 1) <img src> / lazy 属性
    for tag in soup.find_all("img"):
        src = ""
        for attr in ("src", "data-src", "data-original", "data-lazy-src",
                     "data-actualsrc", "data-image", "data-srcset"):
            v = tag.get(attr)
            if v:
                src = v.split(",")[0].strip().split(" ")[0]
                break
        if not src:
            continue
        u = _abs(src, base)
        if not u or u in seen_i:
            continue
        if IMG_EXT.search(u) or urlparse_has_ext(u):
            pass
        elif not u.startswith("http"):
            continue
        bn = os.path.basename(urlsplit(u).path)
        if JUNK_IMG.search(bn) or JUNK_IMG.search(u):
            continue
        if THUMB_SUFFIX.search(bn):     # 缩略图，另找原图
            continue
        if len(bn) <= 4 and not IMG_EXT.search(u):   # 短名多为图标
            continue
        seen_i.add(u)
        images.append({"url": u, "caption": (tag.get("alt") or "").strip()[:200]})

    # 2) <video> / <source>
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src") or tag.get("data-src") or ""
        u = _abs(src, base)
        if u and u not in seen_v:
            seen_v.add(u)
            videos.append({"url": u, "kind": tag.name})

    # 3) <iframe> 视频托管页
    for tag in soup.find_all("iframe"):
        src = tag.get("src") or tag.get("data-src") or ""
        u = _abs(src, base)
        if u and VID_HOST.search(u) and u not in seen_v:
            seen_v.add(u)
            videos.append({"url": u, "kind": "iframe"})

    # 4) og:image / twitter:image 兜底（正文里 <img> 被 JS 渲染时的救命项）
    for prop in ("og:image", "twitter:image", "twitter:image:src"):
        tag = soup.find("meta", attrs={"property": prop}) or \
              soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            u = _abs(tag["content"].strip(), base)
            if u and u not in seen_i and not JUNK_IMG.search(u):
                seen_i.add(u)
                images.append({"url": u, "caption": ""})

    return images, videos


def urlparse_has_ext(u):
    return bool(IMG_EXT.search(u))


def extract_text(soup):
    """提取正文纯文本（保留段落结构）。"""
    body = None
    for sel in CONTENT_SEL:
        try:
            cand = soup.select_one(sel)
        except Exception:
            cand = None
        if cand:
            t = cand.get_text(" ", strip=True)
            if len(t) > 400:
                body = cand
                break
    if body is None:
        body = soup.find("body") or soup
    for tag in list(body.find_all(DROP_TAGS)):
        tag.decompose()
    paras = []
    for el in body.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
        t = el.get_text(" ", strip=True)
        if len(t) >= 25:
            paras.append(t)
    if not paras:
        t = body.get_text("\n", strip=True)
        paras = [x.strip() for x in t.split("\n") if len(x.strip()) >= 25]
    return "\n\n".join(paras)


def fetch_direct(url, timeout=25):
    """直连抓 HTML。返回 (html, note)。"""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception as e:
        return None, f"{type(e).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if any(m in r.text[:4000].lower() for m in CF_MARKERS):
        return None, "CF-challenge"
    return r.content, "direct"


def fetch_jina(url, timeout=30):
    """走 r.jina.ai 阅读器，返回 (html, note)。"""
    try:
        r = requests.get(JINA + url, headers=UA, timeout=timeout)
    except Exception as e:
        return None, f"jina:{type(e).__name__}"
    if r.status_code != 200:
        return None, f"jina HTTP {r.status_code}"
    return r.text, "jina"


def load_queue():
    """读待抓 URL 列表。"""
    for p in ("data/media_queue.json", "out/media_queue.json"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("queue") or list(data.get("items") or [])
            out = []
            for it in data:
                if isinstance(it, str):
                    out.append({"url": it, "id": None})
                else:
                    out.append({"url": it.get("url"), "id": it.get("id")})
            return [x for x in out if x["url"] and not x["url"].startswith("#")]
    p = "out/media_urls.txt"
    if not os.path.exists(p):
        p = "media_urls.txt"
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return [{"url": ln.strip(), "id": None}
                    for ln in f if ln.strip() and not ln.startswith("#")]
    return []


def load_existing():
    for p in ("data/news_media.json", "out/news_media.json"):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
    return {}


def main():
    queue = load_queue()
    if not queue:
        print("[media] 无待抓 URL（缺 data/media_queue.json / out/media_urls.txt）")
        return
    res = load_existing()
    FAILS = []
    print(f"[media] 队列 {len(queue)} 条，已有结果 {len(res)} 条")
    LIMIT_EFF = min(LIMIT, len(queue))
    ok = img_ok = 0
    for i, item in enumerate(queue[:LIMIT_EFF], 1):
        url = item["url"]
        old = res.get(url) or {}
        if old.get("images"):
            print(f"[{i:>3}] SKIP 已有 {len(old['images'])} 图", flush=True)
            img_ok += 1
            ok += 1
            continue
        html, note = fetch_direct(url)
        if not html and not USE_JINA:
            print(f"[{i:>3}] FAIL direct={note}（jina 已禁用）{url[:60]}", flush=True)
            FAILS.append({"url": url, "err": f"direct:{note}", "ts": time.strftime("%F %T")})
            continue
        if html:
            # jina 返回的 markdown 化文本里没有 <img>，直接连的 HTML 才用 soup
            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception:
                soup = None
            if soup is not None:
                text = extract_text(soup)
                images, videos = extract_media(soup, url)
            else:
                text, images, videos = _clean_jina(html), [], []
        else:
            print(f"[{i:>3}] direct 失败({note})，试 jina: {url[:70]}", flush=True)
            jt, jn = fetch_jina(url)
            if not jt:
                # 失败不落盘：留着下次重试（直连失败常是 IP 段轮换/临时挑战）
                print(f"[{i:>3}] FAIL {jn} {url[:70]}", flush=True)
                FAILS.append({"url": url, "err": jn, "ts": time.strftime("%F %T")})
                continue
            text, images, videos = _clean_jina(jt), [], []
            note = jn
            # jina markdown 里的图片语法 ![alt](url)
            for m in re.finditer(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", jt):
                u = m.group(2)
                if not JUNK_IMG.search(u):
                    images.append({"url": u, "caption": m.group(1)[:200]})

        text = (text or "").strip()[:40000]
        if len(text) < 300 and not images:
            print(f"[{i:>3}] THIN text={len(text)} img=0（疑似付费墙/动态渲染）{url[:60]}",
                  flush=True)
            FAILS.append({"url": url, "err": f"thin:{len(text)}",
                          "ts": time.strftime("%F %T")})
            continue
        res[url] = {"text": text, "images": images, "videos": videos,
                    "via": note, "id": item.get("id"),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        ok += 1
        if images:
            img_ok += 1
        print(f"[{i:>3}] OK via={note} text={len(text)} img={len(images)} "
              f"vid={len(videos)} {url[:70]}", flush=True)
        time.sleep(1.0)

    os.makedirs("out", exist_ok=True)
    with open("out/news_media.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open("data/news_media.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    tot_img = sum(len(v.get("images") or []) for v in res.values())
    tot_vid = sum(len(v.get("videos") or []) for v in res.values())
    print(f"\n[media] 成功 {ok}/{LIMIT_EFF}，有图 {img_ok}；"
          f"库内合计 图片 {tot_img} / 视频 {tot_vid}")
    if FAILS:
        os.makedirs("out", exist_ok=True)
        with open("out/media_fails.json", "w", encoding="utf-8") as f:
            json.dump(FAILS, f, ensure_ascii=False, indent=1)
        print(f"[media] 失败 {len(FAILS)} 条（未落盘，下轮重试）-> out/media_fails.json")
        for x in FAILS[:10]:
            print(f"    FAIL {x['err']:<22} {x['url'][:70]}")


if __name__ == "__main__":
    main()
