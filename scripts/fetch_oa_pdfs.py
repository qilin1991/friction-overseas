#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_oa_pdfs.py —— 把 paper 表里「开放获取(OA)」论文的 PDF 抓到腾讯 COS。

只处理 is_oa=1 且有 oa_url 的论文（合法开放获取，不需要 sci-hub）。
非 OA 的付费墙论文一律不动，只留在库里做元数据索引。

流程：
  1. 从 paper 表取 is_oa=1 AND oa_url<>'' AND pdf_cos_key IS NULL 的行
  2. 把 oa_url 规整成「尽量直接的 PDF 链接」（arxiv/abs→/pdf、mdpi 补 /pdf、doi.org 跟随跳转）
  3. 下载，校验 %PDF 魔数 + 体积，失败跳过
  4. 上传 COS：static/papers/paper_{id}.pdf
  5. 回写 pdf_cos_key

用法：
  python3 fetch_oa_pdfs.py --limit 30         # 小批量试跑（dry 仍写库，加 --apply 才上传）
  python3 fetch_oa_pdfs.py --apply --limit 500
  python3 fetch_oa_pdfs.py --apply            # 全量
"""
import os
import re
import sys
import time
import sqlite3
import argparse

sys.path.insert(0, "/opt/friction")
import cos_cred  # noqa: E402  载入 .deploy.env 里的 COS 凭据
from qcloud_cos import CosConfig, CosS3Client  # noqa: E402

DB = "/opt/friction/data/friction.db"
BUCKET = os.environ.get("COS_BUCKET", "qilin-1449368663")
REGION = os.environ.get("COS_REGION", "ap-nanjing")
PREFIX = "static/papers"

_sid, _skey = cos_cred.require()
cos = CosS3Client(CosConfig(Region=REGION, SecretId=_sid,
                            SecretKey=_skey, Token=None))
# CosS3Client 并发 PUT 不稳，上传串行化（下载仍并发）
import threading
_UP_LOCK = threading.Lock()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept": "application/pdf,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}


def normalize(url):
    """尽量把文章页/解析页规整成直链 PDF。"""
    if not url:
        return None
    u = url.strip()
    # arXiv: /abs/XXXX -> /pdf/XXXX.pdf
    m = re.search(r"arxiv\.org/abs/([^/?#]+)", u)
    if m:
        return "https://arxiv.org/pdf/%s.pdf" % m.group(1)
    # MDPI 文章页补 /pdf
    if "mdpi.com" in u and not u.rstrip("/").endswith("/pdf"):
        return u.rstrip("/") + "/pdf"
    # Hindawi / downloads 直链通常已是 PDF
    return u


def fetch_pdf(url, timeout=30):
    """返回 PDF 字节，失败返回 (None, 原因)。"""
    import requests
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup
    try:
        r = requests.get(url, headers=HEADERS, timeout=(8, 20),
                         allow_redirects=True, stream=False)
    except Exception as e:
        return None, "net:%s" % type(e).__name__
    if r.status_code >= 400:
        return None, "http%d" % r.status_code
    ct = r.headers.get("Content-Type", "").lower()
    body = r.content
    if body[:5] == b"%PDF-" and 8000 <= len(body) <= 120 * 1024 * 1024:
        return body, None
    if body[:5] != b"%PDF-" and ct.startswith("text/html"):
        # 落地页：从 HTML 里抠 PDF 直链再试一次
        try:
            soup = BeautifulSoup(body, "html.parser")
            cands = []
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if ".pdf" in h.lower() or "/pdf" in h.lower() or "download" in h.lower():
                    cands.append(urljoin(url, h))
            for m in re.findall(r'(?:pdfUrl|pdf_url|"pdf")\s*[:=]\s*["\']([^"\']+)', r.text):
                cands.append(urljoin(url, m))
            # 只试最像直链 PDF 的，最多 3 个，避免慢超时雪崩
            cands = [c for c in cands if c.lower().endswith(".pdf")
                     or "/pdf" in c.lower()][:3]
            for cu in cands:
                b2, e2 = fetch_pdf(cu, timeout=20)
                if b2:
                    return b2, None
        except Exception:
            pass
        return None, "notpdf:%s" % ct[:20]
    if body[:5] != b"%PDF-":
        return None, "notpdf:%s" % ct[:20]
    if len(body) < 8000:
        return None, "toosmall:%d" % len(body)
    return body, None


def safe_key(pid):
    return "%s/paper_%d.pdf" % (PREFIX, pid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true",
                    help="不传只统计；传了才真正下载+上传+写库")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT id, doi, oa_url FROM paper "
        "WHERE is_oa=1 AND oa_url IS NOT NULL AND oa_url<>'' "
        "AND (pdf_cos_key IS NULL OR pdf_cos_key='') "
        "ORDER BY id").fetchall()]
    if a.limit:
        rows = rows[:a.limit]
    print("[fetch_oa_pdfs] 待处理 OA 论文 %d 篇 (apply=%s)" % (len(rows), a.apply))

    ok = skip = err = 0
    errs = {}
    _lock = __import__("threading").Lock()
    done = [0]

    def work(r):
        pid, oa = r["id"], r["oa_url"]
        url = normalize(oa)
        if not url:
            return "skip", None
        body, e = fetch_pdf(url)
        if body is None:
            return "skip", (pid, e, oa)
        if a.apply:
            key = safe_key(pid)
            try:
                with _UP_LOCK:
                    cos.put_object(Bucket=BUCKET, Key=key, Body=body,
                                   ContentType="application/pdf")
                # 每线程独立连接（sqlite 连接不能跨线程）
                lc = sqlite3.connect(DB)
                lc.execute("UPDATE paper SET pdf_cos_key=? WHERE id=?",
                           (key, pid))
                lc.commit()
                lc.close()
                return "ok", None
            except Exception as ex:
                return "coserr", (pid, str(ex)[:40])
        return "ok", None

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, r): r for r in rows}
        for fu in as_completed(futs):
            st, info = fu.result()
            with _lock:
                done[0] += 1
                i = done[0]
                if st == "ok":
                    ok += 1
                elif st == "skip":
                    skip += 1
                    if info:
                        errs[info[1]] = errs.get(info[1], 0) + 1
                        if skip <= 40:
                            print("  SKIP id=%d %s <- %s" % (info[0], info[1], info[2][:80]))
                else:
                    err += 1
                    if info:
                        errs["cos:%s" % info[1]] = errs.get("cos:%s" % info[1], 0) + 1
                if i % 50 == 0 or i == len(rows):
                    print("  [%d/%d] 成功=%d 跳过=%d COS失败=%d" % (i, len(rows), ok, skip, err), flush=True)

    print("[fetch_oa_pdfs] 完成：成功 %d / 跳过 %d / COS失败 %d" % (ok, skip, err), flush=True)
    if errs:
        print("跳过原因 TOP:", dict(sorted(errs.items(), key=lambda x: -x[1])[:8]), flush=True)


if __name__ == "__main__":
    main()
