# -*- coding: utf-8 -*-
"""
杭州亲子·活动汇 —— 电脑端「搜狗微信」低频发现 + 推送到 VPS

为什么在你自己电脑跑：
  搜狗微信验证码主要看 IP 信誉 + 频率。你家宽带是住宅 IP、还在杭州同城，
  比腾讯云机房 IP 干净得多，最不容易被拦。VPS 只负责政府门户抓取 + 网站展示。

用法（Windows，双击 start_wechat_push.bat 即可，会自动循环）：
  python push_wechat.py           # 跑一轮（拉取查询清单 → 搜狗搜索 → 推送 VPS）
  python push_wechat.py --loop    # 常驻：每轮之间休息 50 分钟
  python push_wechat.py --delay 18
"""
import argparse
import html
import json
import random
import re
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    print("[!] 缺少 requests 库，请先运行：pip install requests -i https://pypi.tuna.tsinghua.edu.cn/simple")
    input("按回车退出...")
    sys.exit(1)

# ================== 配置（如需改 VPS / 口令，只改这里）==================
VPS_BASE = "http://146.56.250.224"          # VPS 地址
AUTH = ("qilin", "0805")                     # 站点口令（nginx Basic Auth）
TOKEN = "tour_push_7f3a9c2e"                 # 推送令牌（与服务端一致）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
ROUND_SLEEP_MIN = 50                         # --loop 时每轮间隔（分钟）

# 拉不到查询清单时的兜底
FALLBACK_QUERIES = [
    {"q": "临平 亲子", "district": "临平"}, {"q": "星桥 活动", "district": "星桥"},
    {"q": "余杭 亲子", "district": "余杭"}, {"q": "萧山 亲子", "district": "萧山"},
    {"q": "杭州 亲子活动", "district": "杭州"}, {"q": "杭州 遛娃", "district": "杭州"},
    {"q": "杭州 周末遛娃", "district": "杭州"}, {"q": "杭州 亲子研学", "district": "杭州"},
]


def clean_title(t):
    t = html.unescape(t or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^\d{1,3}\s+20\d{2}[/-]\d{1,2}", "", t)
    t = re.sub(r"^\d{1,3}\s+", "", t)
    t = re.sub(r"^\d{6}/\d{1,2}", "", t)
    t = re.sub(r"^20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", "", t)
    t = re.sub(r"^20\d{2}年\d{1,2}月\d{1,2}日", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r'(?<=[\u4e00-\u9fff“”‘’《》【】（）])[ \t]+', "", t)
    t = re.sub(r'[ \t]+(?=[\u4e00-\u9fff“”‘’《》【】（）])', "", t)
    return t.strip(" -|·:：")


def parse_sogou(page, district, query):
    """从搜狗微信搜索结果页解析出条目（正则实现，无需 bs4）。"""
    out = []
    for part in re.split(r'<div class="txt-box"', page)[1:]:
        block = part[:2500]
        m = re.search(r'<a[^>]*href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        href = "https://weixin.sogou.com" + m.group(1)
        title = clean_title(re.sub(r"<[^>]+>", "", m.group(2)))
        if len(title) < 6:
            continue
        ts = re.search(r"timeConvert\('(\d+)'\)", block)
        pub = ""
        if ts:
            try:
                pub = datetime.fromtimestamp(int(ts.group(1))).strftime("%Y-%m-%d")
            except Exception:
                pass
        am = re.search(r'class="all-time-y2"[^>]*>(.*?)</span>', block, re.S)
        acct = re.sub(r"<[^>]+>", "", am.group(1)).strip() if am else ""
        # 摘要：搜索结果里的正文开头，常含「活动时间/地点/报名」
        sm = re.search(r'class="txt-info"[^>]*>(.*?)</p>', block, re.S)
        snippet = ""
        if sm:
            snippet = re.sub(r"<!--.*?-->", "", sm.group(1))
            snippet = html.unescape(re.sub(r"<[^>]+>", "", snippet)).strip()
        out.append(dict(title=title, url=href, source="公众号",
                        account=acct or query, district=district,
                        published_at=pub, summary=snippet[:300]))
    return out


def get_queries(sess):
    try:
        r = sess.get(VPS_BASE + "/tour/api/queries", auth=AUTH, timeout=15)
        if r.status_code == 200:
            qs = r.json().get("queries") or []
            if qs:
                return qs
    except Exception as e:
        print("  拉取查询清单失败(%s)，用内置清单" % e)
    return FALLBACK_QUERIES


def one_round(sess, delay):
    queries = get_queries(sess)
    print("本轮查询数：%d" % len(queries))
    items, captcha = [], 0
    for i, item in enumerate(queries, 1):
        q, dist = item["q"], item.get("district") or "杭州"
        try:
            r = sess.get("https://weixin.sogou.com/weixin",
                         params={"type": 2, "query": q}, timeout=20)
        except Exception as e:
            print("  [%02d] %-20s 网络错误 %s" % (i, q, e))
            time.sleep(10)
            continue
        if r.status_code != 200 or "antispider" in r.text or "请输入验证码" in r.text:
            captcha += 1
            print("  [%02d] %-20s [验证码] 冷却 120s" % (i, q))
            if captcha >= 4:
                print("  连续 4 次验证码，本轮停止（下轮再试）")
                break
            time.sleep(120)
            continue
        got = parse_sogou(r.text, dist, q)
        print("  [%02d] %-20s -> %d" % (i, q, len(got)))
        items += got
        time.sleep(delay + random.random() * 6)

    # 批内按标题去重
    seen, uniq = set(), []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        uniq.append(it)
    print("本轮共 %d 条（去重后）" % len(uniq))
    if not uniq:
        print("  无新条目，跳过推送")
        return 0

    try:
        resp = sess.post(VPS_BASE + "/tour/api/ingest", json={"items": uniq},
                         auth=AUTH, headers={"X-Ingest-Token": TOKEN}, timeout=40)
        if resp.status_code == 200:
            j = resp.json()
            print("  推送成功：新增/更新 %s 条，库内共 %s 条" % (j.get("upserted"), j.get("total")))
        else:
            print("  推送失败 HTTP %s：%s" % (resp.status_code, resp.text[:200]))
    except Exception as e:
        print("  推送异常：%s" % e)
    return len(uniq)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="常驻，每轮间隔 ROUND_SLEEP_MIN 分钟")
    ap.add_argument("--delay", type=float, default=14.0, help="每次查询间隔秒数（默认14）")
    args = ap.parse_args()

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Referer": "https://weixin.sogou.com/"})

    if not args.loop:
        print("==== %s 开始一轮 ====" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        one_round(sess, args.delay)
        return

    while True:
        print("\n==== %s 开始一轮 ====" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            one_round(sess, args.delay)
        except Exception as e:
            print("本轮异常：%s" % e)
        print("==== 休息 %d 分钟 ====" % ROUND_SLEEP_MIN)
        time.sleep(ROUND_SLEEP_MIN * 60)


if __name__ == "__main__":
    main()
