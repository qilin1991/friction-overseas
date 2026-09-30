#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VPS 侧：拉取 GitHub Actions 采集的摩擦/制动海外资讯，合并进 friction 站。

复用 study 那套成熟做法：GitHub API(base64) 优先、raw 兜底、带重试。
由 study-overseas-fetch.timer 一起调用（见 --target friction 分支），
或单独用 friction-overseas-fetch.timer 触发。

数据流：
    GitHub Actions (Azure)  →  data/news_overseas.json (仓库)
                            →  本脚本
                            →  /opt/friction/data/friction.db  (article 表)

幂等：按 url 去重（content_hash 唯一约束兜底），重复拉不会重复入库。
容错：拉取失败打印 FETCH_FAIL 并返回 0，不影响 timer 下一轮。
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

# friction 项目路径（可用环境变量覆盖）
FRICTION_HOME = os.environ.get("FRICTION_HOME", "/opt/friction")
sys.path.insert(0, FRICTION_HOME)

REPO = os.environ.get("OVERSEAS_REPO", "qilin1991/friction-overseas")
TOKEN = os.environ.get("GH_TOKEN", "").strip()
UA = {"User-Agent": "Friction-VPS-Merger"}

# 仓库内文件路径 —— 只认 friction 专用文件。
#
# ⚠️ 务必不要回退到 study 的 news_overseas.json！
# 那个文件装的是 study 项目的通用新闻（bbc/cnn/体育/时政），
# 一旦被拉进来会污染 friction 库（2026-09-30 已踩过此坑）。
CANDIDATE_PATHS = [
    "data/news_overseas.json",
    "news_overseas.json",
]

# 允许入库的信源白名单：Actions 侧 collect_overseas.py 里配置的那些。
# 只要条目 source 不在名单里，一律丢弃 —— 双保险，防止再混入无关内容。
ALLOWED_SOURCES = {
    "SAE International", "Bosch 新闻室", "US EPA",
    "Counterman", "Tire Business", "Fleet Owner", "CCJ Digital",
    "Underhood Service", "Aftermarket News", "TMD Friction",
    # 聚合兜底产出的源名（格式为 "<站点名>(聚合)"）
    "Counterman(聚合)", "Tire Business(聚合)", "Fleet Owner(聚合)",
    "CCJ Digital(聚合)", "Underhood Service(聚合)", "Aftermarket News(聚合)",
    "TMD Friction(聚合)", "UNECE WP.29(聚合)", "Espacenet(聚合)",
}

# 兜底：即便 source 名不全等，只要带这些行业词也放行
TOPIC_HINTS = [
    "friction", "brake", "pad", "lining", "tribology", "rotor", "caliper",
    "制动", "摩擦", "刹车", "衬片", "闸片",
]


def is_allowed(rec: dict) -> bool:
    """条目是否属于 friction 主题（信源白名单 + 关键词双重校验）。"""
    sname = (rec.get("source") or "").strip()
    if sname in ALLOWED_SOURCES:
        return True
    text = ((rec.get("title") or "") + " " +
            (rec.get("title_zh") or "") + " " +
            (rec.get("tag") or "")).lower()
    return any(h in text for h in TOPIC_HINTS)


def norm_url(u):
    u = (u or "").strip().split("?", 1)[0].rstrip("/")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    return re.sub(r"^www\.", "", u, flags=re.I)


def _try_parse(raw: bytes):
    """GitHub API 返回的可能是 base64 包装，也可能是裸 JSON。"""
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if isinstance(d, dict) and "content" in d and "articles" not in d:
        try:
            d = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
        except Exception:
            return None
    rows = d.get("articles") if isinstance(d, dict) else d
    return rows or None


def fetch_rows():
    """依次尝试各候选路径；每路径先 API 后 raw。"""
    hdr = dict(UA)
    if TOKEN:
        hdr["Authorization"] = "token " + TOKEN
        hdr["Accept"] = "application/vnd.github+json"

    for path in CANDIDATE_PATHS:
        # 1) GitHub API（base64），比 raw CDN 稳
        api = f"https://api.github.com/repos/{REPO}/contents/{path}"
        for i in range(4):
            try:
                r = requests.get(api, headers=hdr, timeout=90)
                if r.status_code == 200:
                    rows = _try_parse(r.content)
                    if rows:
                        print(f"  来源: API {path}（{len(rows)} 条）")
                        return rows
                elif r.status_code == 404:
                    break          # 该路径不存在，换下一个
                else:
                    print(f"  API {r.status_code} ({path})")
            except Exception as e:
                print(f"  API retry {i} {path}: {type(e).__name__}")
                time.sleep(3)

        # 2) raw 兜底
        raw = f"https://raw.githubusercontent.com/{REPO}/main/{path}"
        for i in range(2):
            try:
                r = requests.get(raw, headers=UA, timeout=60)
                if r.status_code == 200:
                    rows = _try_parse(r.content)
                    if rows:
                        print(f"  来源: raw {path}（{len(rows)} 条）")
                        return rows
            except Exception as e:
                print(f"  raw retry {i} {path}: {type(e).__name__}")
                time.sleep(3)
    return None


def merge_into_friction(rows):
    """把海外条目写进 friction 的 article 表。"""
    from app import create_app
    from app.services import pipeline
    from app.services.collectors import normalize
    from app.models import Source

    # 先过白名单，杜绝无关内容混入（study 通用新闻等）
    kept_rows = [a for a in rows if is_allowed(a)]
    dropped = len(rows) - len(kept_rows)
    if dropped:
        print(f"  已过滤非摩擦主题条目 {dropped} 条（{len(rows)} → {len(kept_rows)}）")
    if not kept_rows:
        print("  无符合 friction 主题的条目，跳过")
        return 0

    app = create_app()
    with app.app_context():
        pipeline.seed_sources()
        trust = {s["name"]: s.get("trust", 0.82) for s in Source.all()}

        # 按 source 分组，各组用自己的可信度
        buckets = {}
        for a in kept_rows:
            sname = (a.get("source") or "海外聚合").strip()
            buckets.setdefault(sname, []).append(a)

        total_in = 0
        for sname, items_raw in buckets.items():
            items = []
            for a in items_raw:
                # 优先用中文标题（Actions 侧已翻译）
                title = (a.get("title_zh") or a.get("title") or "").strip()
                if len(title) < 6:
                    continue
                rec = normalize({
                    "title": title,
                    "url": a.get("url", ""),
                    "raw_content": a.get("title", ""),
                    "published_at": a.get("collected_at"),
                }, source_name=sname)
                rec["region"] = a.get("region", "海外")
                rec["lang"] = "zh" if a.get("title_zh") else a.get("lang", "en")
                items.append(rec)
            if not items:
                continue
            r = pipeline.ingest(items, source_trust=trust.get(sname, 0.82), verbose=False)
            got = r.get("ingested", 0)
            total_in += got
            print(f"    {sname:<26} 投入 {len(items):>3}，入库 {got:>3}")
        return total_in


def main():
    print(f"[friction-overseas] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    rows = fetch_rows()
    if not rows:
        print("FETCH_FAIL（远端无数据或网络不通，跳过本轮）")
        return 0                      # 返回 0，不让 timer 报错
    try:
        added = merge_into_friction(rows)
    except Exception as e:
        print(f"MERGE_ERR: {type(e).__name__}: {repr(e)[:160]}")
        return 1
    print(f"MERGED 远端 {len(rows)} 条，新增入库 {added} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
