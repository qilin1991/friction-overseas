# -*- coding: utf-8 -*-
"""杭州活动汇 · /tour 分站（Flask + waitress 127.0.0.1:8090）
汇总临平/星桥/余杭/萧山/浙江等地政府、街道、社区、共青团公众号里可报名参加的活动。
"""
import os
import re
import sqlite3
from datetime import date

from flask import Flask, g, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "tour.db")

app = Flask(__name__, static_url_path="/tour/static",
            static_folder=os.path.join(BASE, "static"))


def get_conn():
    if "db" not in g:
        c = sqlite3.connect(DB, timeout=20)
        c.row_factory = sqlite3.Row
        g.db = c
    return g.db


@app.teardown_appcontext
def _close(_exc):
    c = g.pop("db", None)
    if c is not None:
        c.close()


def q(sql, args=()):
    return get_conn().execute(sql, args).fetchall()


def _status(row):
    """只有【活动日期】已过才算结束；报名截止过了不算（可能已报名，活动仍可参加）。"""
    today = date.today().isoformat()
    v = (row["event_date"] or "").strip()
    if v:
        m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", v)
        if m:
            d = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
            if d < today:
                return "closed"
        else:
            m2 = re.search(r"(\d{1,2})月(\d{1,2})日", v)
            if m2:
                d = "%d-%02d-%02d" % (date.today().year, int(m2.group(1)), int(m2.group(2)))
                if d < today:
                    return "closed"
    if any(k in (row["title"] or "") for k in ("报名", "招募", "征集")):
        return "open"
    return "recent"


@app.get("/tour/")
def index():
    district = request.args.get("district") or ""
    category = request.args.get("category") or ""
    stype = request.args.get("stype") or ""
    only_open = request.args.get("open") == "1"
    family = request.args.get("family") == "1"
    days = request.args.get("days") or "120"
    kw = (request.args.get("kw") or "").strip()

    where, args = ["1=1"], []
    if district:
        where.append("district=?"); args.append(district)
    if category:
        where.append("category=?"); args.append(category)
    if stype == "gov":
        where.append("source NOT IN ('公众号','豆瓣同城','大麦','活动行')")
    elif stype == "wechat":
        where.append("source='公众号'")
    elif stype == "douban":
        where.append("source='豆瓣同城'")
    elif stype == "damai":
        where.append("source='大麦'")
    elif stype == "huodongxing":
        where.append("source='活动行'")
    if family:
        where.append("category IN ('亲子','展览','演出','市集','文体','志愿','文旅','培训')")
    try:
        d = max(1, min(3650, int(days)))
    except ValueError:
        d = 120
    where.append("(published_at IS NULL OR published_at = '' OR published_at >= date('now', ?))")
    args.append("-%d day" % d)
    if kw:
        where.append("(title LIKE ? OR summary LIKE ?)")
        args += ["%" + kw + "%", "%" + kw + "%"]
    w = " AND ".join(where)

    # 排序：近期活动优先（活动日 >= 今天的，按活动日由近到远），其余按发布时间倒序
    _sort = ("ORDER BY "
             "CASE WHEN event_date<>'' AND event_date>=date('now') THEN 0 ELSE 1 END, "
             "CASE WHEN event_date<>'' AND event_date>=date('now') THEN event_date END ASC, "
             "COALESCE(published_at,'') DESC, id DESC")
    rows = q("SELECT * FROM event WHERE %s %s LIMIT 400" % (w, _sort), args)
    rows = [dict(r) for r in rows]
    for r in rows:
        r["status2"] = _status(r)
    if only_open:
        rows = [r for r in rows if r["status2"] == "open"]

    districts = [r[0] for r in q(
        "SELECT district FROM event WHERE district IS NOT NULL AND district<>'' "
        "GROUP BY district ORDER BY COUNT(*) DESC")]
    cats = [r[0] for r in q(
        "SELECT category FROM event WHERE category IS NOT NULL AND category<>'' "
        "GROUP BY category ORDER BY COUNT(*) DESC")]
    total = q("SELECT COUNT(*) FROM event")[0][0]
    last = q("SELECT MAX(last_seen) FROM event")[0][0] or "—"
    n_all = total
    n_gov = q("SELECT COUNT(*) FROM event WHERE source NOT IN ('公众号','豆瓣同城','大麦','活动行')")[0][0]
    n_wechat = q("SELECT COUNT(*) FROM event WHERE source='公众号'")[0][0]
    n_douban = q("SELECT COUNT(*) FROM event WHERE source='豆瓣同城'")[0][0]
    n_damai = q("SELECT COUNT(*) FROM event WHERE source='大麦'")[0][0]
    n_hx = q("SELECT COUNT(*) FROM event WHERE source='活动行'")[0][0]

    return render_template("index.html", rows=rows, districts=districts, cats=cats,
                           district=district, category=category, stype=stype,
                           only_open=only_open, family=family, days=days, kw=kw,
                           total=total, last=last, n_all=n_all, n_gov=n_gov,
                           n_wechat=n_wechat, n_douban=n_douban, n_damai=n_damai, n_hx=n_hx)


@app.get("/tour/sources")
def sources():
    import sources as S
    from urllib.parse import quote
    order = {g: i for i, g in enumerate(S.GROUP_ORDER)}

    DIST_ORDER = ["临平·星桥", "临平", "上城", "拱墅", "西湖", "滨江", "钱塘", "余杭", "萧山"]

    def dkey(d):
        for i, p in enumerate(DIST_ORDER):
            if d.startswith(p):
                return (i, d)
        return (9, d)

    accs = [dict(name=n, group=g, district=d,
                 url="https://weixin.sogou.com/weixin?type=2&query=" + quote(n))
            for n, g, d in S.ACCOUNTS]
    accs.sort(key=lambda x: (order.get(x["group"], 99), dkey(x["district"]), x["name"]))
    return render_template("sources.html", accounts=accs, sites=S.SITES,
                           total=len(accs), groups=S.GROUP_ORDER)


@app.get("/tour/api/events")
def api_events():
    rows = q("SELECT title,url,source,account,district,category,published_at,event_date,"
             "location,summary FROM event ORDER BY COALESCE(published_at,'') DESC LIMIT 200")
    return {"items": [dict(r) for r in rows]}


# 电脑端（住宅 IP）搜狗发现结果推送入口。双重保护：nginx Basic Auth + 本 token。
INGEST_TOKEN = os.environ.get("TOUR_INGEST_TOKEN", "tour_push_7f3a9c2e")


@app.get("/tour/api/queries")
def api_queries():
    """给电脑端脚本下发搜狗查询矩阵，保证与 VPS 一致。"""
    import collect_tour as C
    return {"queries": [{"q": q, "district": d} for q, d in C.SOGOU_QUERIES]}


@app.post("/tour/api/ingest")
def api_ingest():
    if request.headers.get("X-Ingest-Token", "") != INGEST_TOKEN:
        return {"ok": False, "error": "bad token"}, 403
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return {"ok": True, "upserted": 0, "total": q("SELECT COUNT(*) FROM event")[0][0]}

    import collect_tour as C
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=180)).strftime("%Y-%m-%d")
    HZ_KW = ("杭州", "临平", "余杭", "萧山", "钱塘", "富阳", "临安", "桐庐", "建德",
             "拱墅", "上城", "滨江", "星桥", "西湖", "浙江")
    OTHER_CITY = ("德州", "北京", "上海", "南京", "苏州", "广州", "深圳", "成都", "武汉",
                  "西安", "天津", "济南", "青岛", "郑州", "长沙", "重庆", "宁波", "绍兴",
                  "嘉兴", "湖州", "金华", "温州", "台州", "合肥")
    conn = C.db()
    n = 0
    try:
        for it in items:
            title = C.clean_title(it.get("title") or "")
            _src = it.get("source") or "公众号"
            _native = _src in ("大麦", "活动行", "豆瓣同城")   # 本身就是活动，不必按家庭关键词过滤
            if len(title) < 4:
                continue
            if not _native and (len(title) < 6 or not C.is_joinable(title)):
                continue
            _pub = (it.get("published_at") or "").strip()
            if _pub and _pub < cutoff:          # 过期老文章
                continue
            if not _native and any(w in title for w in OTHER_CITY) and not any(k in title for k in HZ_KW):
                continue                          # 外地噪声
            _sum = it.get("summary") or ""
            _ev, _loc, _sg = C.extract_fields(_sum + " " + title)   # 从摘要抽活动时间/地点/报名
            if not _ev:
                _ev = C.guess_event_date(_sum, _pub)               # 摘要里裸日期粗猜（带窗口）
            _dist = C.norm_district(it.get("district") or "杭州")
            if _native:   # 豆瓣/大麦/活动行：按地址里的区县归位
                _dist = C.guess_district((it.get("location") or _loc) + " " + title) or _dist
            rec = C._rec(
                title, it.get("url") or "", _src,
                it.get("account") or "", _dist,
                it.get("column") or "", _pub,
                _sum, it.get("event_date") or _ev,
                it.get("location") or _loc, it.get("signup_method") or _sg)
            if C.upsert(conn, rec):
                n += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "upserted": n, "total": total}
