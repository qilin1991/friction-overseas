# -*- coding: utf-8 -*-
"""杭州亲子·活动汇 采集器  →  tour.db

源（方案4：深挖政府栏目，覆盖全杭州）：
  gov   全杭州政府门户（JPAAS 集约化平台用 build/unit 抓 JS 栏目；老 CMS 静态解析）
        自动跟进首页浮现的栏目页（/col/col*/index.html），再按「亲子/家庭游玩」分类
  sogou 搜狗微信（指定公众号+关键词，有反爬，best-effort）
  bdb   杭州本地宝「每月活动」（默认不并入，易过期）

用法：
  python collect_tour.py                # 全部（gov + sogou）
  python collect_tour.py --only gov
  python collect_tour.py --only sogou
  python collect_tour.py --only bdb
  python collect_tour.py --days 90
"""
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "tour.db")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 全杭州门户：区/县 + 市级 + 文旅（带老婆孩子玩最相关）
PORTALS = [
    ("杭州",   "https://www.hangzhou.gov.cn",            "杭州市政府"),
    ("上城",   "https://www.hzsc.gov.cn",                "上城区政府"),
    ("拱墅",   "https://www.gongshu.gov.cn",             "拱墅区政府"),
    ("滨江",   "https://www.hhtz.gov.cn",                "滨江区政府"),
    ("萧山",   "https://www.xiaoshan.gov.cn",            "萧山区政府"),
    ("余杭",   "https://www.yuhang.gov.cn",              "余杭区政府"),
    ("临平",   "https://www.linping.gov.cn",             "临平区政府"),
    ("钱塘",   "https://www.qiantang.gov.cn",            "钱塘区政府"),
    ("富阳",   "https://www.fuyang.gov.cn",              "富阳区政府"),
    ("临安",   "https://www.linan.gov.cn",               "临安区政府"),
    ("桐庐",   "https://www.tonglu.gov.cn",              "桐庐县政府"),
    ("建德",   "https://www.jiande.gov.cn",              "建德市政府"),
    ("文旅",   "https://wgly.hangzhou.gov.cn",           "杭州文旅"),
]
# 待确认域名（VPS 解析不到）：西湖(xihuhangzhou.gov.cn) / 淳安(chunan.gov.cn)
# 上城区官方为 hzsc.gov.cn；滨江用高新区 hhtz.gov.cn；文旅杭州为 wgly.hangzhou.gov.cn

WECHAT_ACCOUNTS = ["临平发布", "星桥发布", "星桥街道发布", "贾家社区发布",
                   "青春浙江", "萧山共青团", "青春余杭", "青春临平", "青春杭州"]

# 搜狗查询矩阵：账号名 + 街道/区域 + 市级亲子遛娃 + 活动类型（覆盖"没关注的号"与"小号"）
SOGOU_QUERIES = [
    # 关注账号（账号名搜索，收录时有时无）
    ("临平发布", "临平"), ("星桥发布", "星桥"), ("星桥街道发布", "星桥"),
    ("贾家社区发布", "星桥"), ("青春浙江", "浙江"), ("萧山共青团", "萧山"),
    ("青春余杭", "余杭"), ("青春临平", "临平"), ("青春杭州", "杭州"),
    # 街道 / 区域 + 活动
    ("星桥 活动", "星桥"), ("乔司 活动", "临平"), ("崇贤 活动", "临平"),
    ("塘栖 活动", "临平"), ("运河 活动", "临平"), ("超山 活动", "临平"),
    ("艺尚小镇 活动", "临平"), ("临平 亲子", "临平"), ("临平 遛娃", "临平"),
    ("余杭 亲子", "余杭"), ("萧山 亲子", "萧山"), ("钱塘 亲子", "钱塘"),
    ("富阳 亲子", "富阳"), ("临安 亲子", "临安"), ("桐庐 亲子", "桐庐"),
    ("建德 亲子", "建德"), ("滨江 亲子", "滨江"), ("拱墅 亲子", "拱墅"),
    ("上城 亲子", "上城"),
    # 市级 / 亲子遛娃
    ("杭州 亲子活动", "杭州"), ("杭州 遛娃", "杭州"), ("杭州 周末遛娃", "杭州"),
    ("杭州 亲子研学", "杭州"), ("杭州 儿童剧", "杭州"), ("杭州 展览 亲子", "杭州"),
    ("杭州 市集", "杭州"), ("杭州 图书馆 活动", "杭州"), ("杭州 博物馆 活动", "杭州"),
    ("杭州 少年宫", "杭州"), ("杭州 公益 课堂", "杭州"), ("杭州 志愿服务 招募", "杭州"),
    ("杭州 公园 活动", "杭州"), ("杭州 露营", "杭州"), ("杭州 徒步 亲子", "杭州"),
    # 活动类型
    ("杭州 手工 亲子", "杭州"), ("杭州 自然 教育", "杭州"), ("杭州 农场 研学", "杭州"),
    ("杭州 非遗 体验", "杭州"), ("杭州 亲子 剧场", "杭州"),
    # 高信号：以「活动报名 / 招募」为核心
    ("活动报名 杭州", "杭州"), ("活动报名 临平", "临平"), ("活动报名 星桥", "星桥"),
    ("活动报名 余杭", "余杭"), ("活动报名 萧山", "萧山"),
    ("星桥 报名", "星桥"), ("星桥 亲子", "星桥"), ("星桥 社区 活动", "星桥"),
    ("招募 杭州 亲子", "杭州"), ("亲子 招募 临平", "临平"),
    ("杭州 青少年 活动", "杭州"), ("杭州 儿童 活动", "杭州"),
]

# 自动派生：sources.py 里所有「街道·社区」账号一律纳入采集查询（新增号只改 sources.py 一处）
try:
    import sources as _SRC
    for _n, _g, _d in _SRC.ACCOUNTS:
        if _g == "街道·社区":
            _dist = "星桥" if _d.startswith("临平·星桥") else _d.split("·")[0]
            if all(_n != q for q, _ in SOGOU_QUERIES):
                SOGOU_QUERIES.append((_n, _dist))
except Exception as _e:
    print("  [warn] 未能从 sources.py 派生街道号:", _e)
ACCOUNT_DISTRICT = {
    "临平发布": "临平", "星桥发布": "星桥", "星桥街道发布": "星桥",
    "贾家社区发布": "星桥", "青春浙江": "浙江", "萧山共青团": "萧山",
    "青春余杭": "余杭", "青春临平": "临平", "青春杭州": "杭州",
}

# 带娃家庭出游「可参加 / 可去玩」的强信号词（任一命中即候选）
JOIN_KW = [
    # 可报名/可参加
    "报名", "招募", "招募令", "开放报名", "报名中", "名额", "扫码",
    "亲子", "青少年", "研学", "夏令营", "市集", "集市", "展览", "展会", "演出", "游园",
    "嘉年华", "开放日", "讲座", "比赛", "竞赛", "文明实践", "志愿服务", "义诊", "庙会",
    "灯会", "灯光秀", "烟花", "马拉松", "亲子活动", "少儿", "儿童", "宝宝", "家庭", "适龄",
    "体验课", "公益课", "读书会", "沙龙", "分享会", "敬老", "重阳", "邻里", "花海", "赏桂",
    "赏花", "桂花", "露营", "骑行", "徒步", "采摘", "秘境", "打卡", "非遗", "户外",
    "消费季", "创意生活", "邻居节", "文化节", "丰收节", "音乐节", "艺术节", "戏剧", "话剧",
    "儿童剧", "音乐会", "演唱会", "画展", "摄影展", "书展", "游学", "手工", "烘焙", "插花",
    "茶艺", "瑜伽", "舞蹈", "合唱", "朗诵", "活动", "趣玩", "亲子游", "春游", "秋游",
    "研学游", "采摘节", "动漫", "音乐", "科普", "惠民",
]
# 纯政务/会议/新闻/经贸/政策，命中即剔除（即使标题含活动/文旅等）
NEG_KW = ["通告", "公告", "通知", "部署会", "座谈会", "慰问", "换届", "任免", "政策解读",
          "管理办法", "公开信", "决定", "召开", "举行", "调研", "检查", "督查", "走访",
          "总结会", "推进会", "动员会", "表彰", "评选结果", "启动", "开工", "竣工", "投产",
          "签约", "协议", "招商", "拆迁", "规划", "条例", "法规", "办法", "批复", "民生实事",
          "意见征集", "项目征集", "采购", "招标", "公示", "通报", "简报", "成绩", "分数线",
          "录取", "产值", "GDP", "利润", "收入", "增长", "会议", "常委", "常务", "强调",
          "批示", "讲话", "报告", "调度", "演练", "消防", "安全", "审批", "许可", "备案",
          "办证", "执照", "税务", "注销登记", "变更登记", "登记", "签注", "核准", "名单",
          "招考", "录用", "天气", "整改", "初心", "贯彻", "从严", "叩", "决算", "预算",
          "审计", "以旧换新", "消费券", "印发", "方案", "细则", "体系", "产业", "经济",
          "高质量", "生产力", "集群", "企业", "项目", "补贴", "补助", "保障", "委员会",
          "办公室", "政协", "人大", "档案", "征集", "公开征集", "解读", "图解", "公布",
          "改革", "扫黑", "反腐", "纪律", "纪委", "监委", "巡视", "问政", "电视", "部署",
          "发展", "建设", "创新", "赋能", "平台", "数字", "智能", "科创", "新质", "现代化",
          "一流", "工作", "开展", "组织", "实施", "关于", "加快", "推动", "研究", "审议",
          "爆竹", "赛事", "夺冠", "首胜", "战胜", "亮相", "比分", "决赛", "季军", "亚军",
          "招聘", "就业", "岗位", "用工", "求职", "面试", "人才引进", "事业单位", "劳动合同制",
          "应届毕业", "医学类", "教师招聘", "公开招聘", "拟聘", "劳务派遣",
          "昨日", "上周", "日前", "圆满", "落幕", "收官", "回顾", "成功举办", "顺利举办", "已开跑"]
EXPLICIT = ["报名", "招募", "名额", "扫码", "开放报名", "报名中"]

CAT_MAP = [
    ("亲子", ["亲子", "儿童", "少儿", "小朋友", "家长", "幼儿园", "小学", "暑期", "夏令营", "研学", "宝宝", "家庭", "少年", "幼儿"]),
    ("展览", ["展览", "展会", "画展", "摄影展", "书展", "博物", "美术馆", "非遗展"]),
    ("演出", ["演出", "晚会", "话剧", "儿童剧", "音乐会", "演唱会", "戏曲", "文艺", "秀", "灯光秀", "烟花"]),
    ("市集", ["市集", "集市", "文创", "摊位", "跳蚤", "夜市"]),
    ("青年", ["青年", "团员", "团干", "大学生", "青年之家", "交友", "联谊", "青工"]),
    ("志愿", ["志愿", "义工", "公益", "文明实践", "雷锋", "服务队", "无偿献血", "义诊"]),
    ("文体", ["文体", "运动会", "球赛", "篮球", "足球", "羽毛球", "跑步", "徒步", "骑行",
              "书画", "电影", "游园", "庙会", "灯会", "马拉松", "嘉年华"]),
    ("培训", ["培训", "讲座", "课堂", "沙龙", "分享会", "读书会", "技能", "考证", "辅导", "公益课", "体验课"]),
    ("文旅", ["文旅", "旅游", "景区", "游玩", "惠民", "免费游", "打卡"]),
]


def classify(text):
    for cat, kws in CAT_MAP:
        if any(k in text for k in kws):
            return cat
    return "其他"


def clean_title(t):
    t = html.unescape(t or "")     # &mdash; &ldquo; &amp; 等实体还原
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^\d{1,3}\s+20\d{2}[/-]\d{1,2}", "", t)      # 前导 "16 2026/10"
    t = re.sub(r"^\d{1,3}\s+", "", t)                          # 前导序号 "16 "
    t = re.sub(r"^\d{6}/\d{1,2}", "", t)
    t = re.sub(r"^20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", "", t)
    t = re.sub(r"^20\d{2}年\d{1,2}月\d{1,2}日", "", t)
    t = re.sub(r"\s*20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*$", "", t)
    t = re.sub(r"\s*20\d{2}年\d{1,2}月\d{1,2}日\s*$", "", t)
    # 去掉搜狗在中文/标点之间插入的高亮空格："活动 报名“ 星动 ”" -> "活动报名“星动”"
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r'(?<=[\u4e00-\u9fff“”‘’《》【】（）])[ \t]+', "", t)
    t = re.sub(r'[ \t]+(?=[\u4e00-\u9fff“”‘’《》【】（）])', "", t)
    return t.strip(" -|·:：")


def is_joinable(title, text=""):
    s = (title or "") + " " + (text or "")
    if not any(k in s for k in JOIN_KW):
        return False
    if any(k in (title or "") for k in NEG_KW):
        if not any(k in (title or "") for k in EXPLICIT):
            return False
    return True


def extract_fields(text):
    t = re.sub(r"\s+", " ", text or "")
    ev = ""
    m = re.search(r"(?:活动时间|活动日期|时间|日期)[:：]\s*([0-9]{1,2}月[0-9]{1,2}日[^ ]{0,26})", t)
    if m:
        ev = m.group(1).strip()
    loc = ""
    m = re.search(r"(?:活动地点|地点|地址)[:：]\s*([^ ]{2,40})", t)
    if m:
        loc = m.group(1).strip()
    signup = ""
    m = re.search(r"(?:报名(?:方式|入口|链接)?|参与方式)[:：]\s*([^ ]{2,70})", t)
    if m:
        signup = m.group(1).strip()
    return ev, loc, signup


def uid_of(title, district, src, col):
    return hashlib.md5((title + "|" + (district or "") + "|" + (src or "") + "|" + (col or "")).encode("utf-8")).hexdigest()[:16]


def norm_url(url):
    """归一化文章 URL 用于去重。
    ⚠️ 搜狗公众号链接 https://weixin.sogou.com/link?url=XXX 的唯一标识在 query 里，
    绝不能剥掉 query，否则所有公众号文章会塌成同一个 uid 互相覆盖。"""
    u = (url or "").split("#")[0]
    try:
        from urllib.parse import urlparse
        p = urlparse(u)
        host = p.netloc.lower()
        if "sogou.com" in host:
            return u.lower()          # 保留完整 query 作为唯一标识
        return p.scheme.lower() + "://" + host + p.path
    except Exception:
        return u.lower()


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def ensure_schema():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS event (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      uid TEXT UNIQUE,
      title TEXT NOT NULL,
      url TEXT,
      source TEXT,
      account TEXT,
      district TEXT,
      column TEXT,
      category TEXT,
      published_at TEXT,
      event_date TEXT,
      location TEXT,
      signup_deadline TEXT,
      signup_method TEXT,
      summary TEXT,
      status TEXT DEFAULT 'open',
      first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
      last_seen TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tour_pub ON event(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tour_district ON event(district);
    CREATE INDEX IF NOT EXISTS idx_tour_cat ON event(category);
    """)
    c.commit()
    c.close()


def upsert(conn, rec):
    """幂等写入；重抓时保留已 enrich 的活动时间/地点/报名/摘要（新值为空则沿用旧值）。"""
    try:
        conn.execute("""
        INSERT INTO event(uid,title,url,source,account,district,column,category,published_at,
                          event_date,location,signup_deadline,signup_method,summary,status,last_seen)
        VALUES(:uid,:title,:url,:source,:account,:district,:column,:category,:published_at,
               :event_date,:location,:signup_deadline,:signup_method,:summary,:status,:last_seen)
        ON CONFLICT(uid) DO UPDATE SET
          title=excluded.title, url=excluded.url, category=excluded.category,
          column=COALESCE(NULLIF(excluded.column,''), event.column),
          published_at=COALESCE(NULLIF(excluded.published_at,''), event.published_at),
          event_date=COALESCE(NULLIF(excluded.event_date,''), event.event_date),
          location=COALESCE(NULLIF(excluded.location,''), event.location),
          signup_method=COALESCE(NULLIF(excluded.signup_method,''), event.signup_method),
          summary=COALESCE(NULLIF(excluded.summary,''), event.summary),
          last_seen=excluded.last_seen
        """, rec)
        return True
    except Exception as e:
        print("  upsert err:", e)
        return False


def _rec(title, url, source, account, district, col, pub, summary="", ev="", loc="", signup=""):
    # 公众号：搜狗 link 的 token 每次不同，用「标题+账号」做稳定去重键；其余按归一化 URL
    if source == "公众号":
        key = "T|" + (title or "") + "|" + (district or "")
    else:
        key = norm_url(url)
    return dict(uid=hashlib.md5(key.encode("utf-8")).hexdigest()[:16], title=title, url=url, source=source,
                account=account, district=district, column=col or "", category=classify(title + " " + summary),
                published_at=pub, event_date=ev, location=loc, signup_deadline="",
                signup_method=signup, summary=summary[:200], status="open",
                last_seen=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# ----------------------- JPAAS / 静态栏目抓取 -----------------------
def extract_query_datas(html):
    out = []
    for m in re.finditer(r'queryData="(\{[^"]*\})"', html):
        try:
            out.append(json.loads(m.group(1).replace("'", '"')))
        except Exception:
            pass
    return out


def jpaas_unit(base, qd, sess):
    ep = base.rstrip("/") + "/api-gateway/jpaas-publish-server/front/page/build/unit"
    try:
        r = sess.get(ep, params=qd, timeout=15, headers={"Referer": base})
        if r.status_code == 200:
            try:
                j = r.json()
                if j.get("success"):
                    return j.get("data", {}).get("html", "")
            except Exception:
                pass
    except Exception:
        pass
    return ""


def parse_links(html, base):
    """从一段 HTML 抽出文章/栏目链接，返回 [(title, href, near)]，near=邻近日期文本。"""
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for a in soup.find_all("a"):
        t = clean_title(a.get_text(" ", strip=True))
        h = a.get("href") or ""
        if len(t) < 6:
            continue
        if h.startswith("//"):
            h = "https:" + h
        elif h.startswith("/"):
            h = base.rstrip("/") + h
        elif not h.startswith("http"):
            continue
        # 邻近日期：父 li/div/tr 的文本
        near = ""
        p = a.find_parent(["li", "div", "tr", "dd"])
        if p:
            near = p.get_text(" ", strip=True)
        out.append((t, h, near))
    return out


def extract_date(text, url):
    """优先从邻近文本取 年-月-日；其次从 URL（需年+月）。返回 YYYY-MM-DD 或 ''。"""
    if text:
        m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if m:
            return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(?<!\d)(\d{1,2})[-/.月](\d{1,2})(?!\d)", text)
        if m:
            y = datetime.now().year
            return "%d-%02d-%02d" % (y, int(m.group(1)), int(m.group(2)))
    m = re.search(r"/art/(\d{4})[-/](\d{1,2})[-/](\d{1,2})", url)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return ""


def parse_date(v):
    """把 '10月14日' / '2026-10-14' / '2026.10.14' 解析为 YYYY-MM-DD，失败返回 None。"""
    if not v:
        return None
    s = str(v)
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s*[-/.月]\s*(\d{1,2})", s)
    if m:
        return "%d-%02d-%02d" % (datetime.now().year, int(m.group(1)), int(m.group(2)))
    return None


def guess_event_date(text, pub):
    """从摘要/正文粗猜活动日期：取「M月D日」，且与发布时间相差在 [-7, +180] 天内才采信。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text or "")
    if not m:
        return ""
    mo, dd = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= dd <= 31):
        return ""
    y = int(pub[:4]) if re.match(r"\d{4}", pub or "") else datetime.now().year
    try:
        cand = datetime(y, mo, dd).date()
    except ValueError:
        return ""
    if pub:
        try:
            pd = datetime.strptime(pub, "%Y-%m-%d").date()
            diff = (cand - pd).days
            if diff < -7:
                # 仅当「11/12月发布 → 1/2月活动」才视为跨年，其余早于发布日的日期不采信
                if mo in (1, 2) and pd.month in (11, 12):
                    try:
                        cand2 = cand.replace(year=cand.year + 1)
                        if 0 <= (cand2 - pd).days <= 90:
                            return cand2.isoformat()
                    except ValueError:
                        pass
                return ""
            if diff > 180:
                return ""
        except Exception:
            pass
    return cand.isoformat()


def expire_sweep(conn, undated_days=45):
    """只按【活动日期】过期删除；报名截止过了不删（用户可能已报名，活动仍在）。
    无活动日期的，仅按发布时间兜底 TTL（默认 45 天）。返回删除数。"""
    today = datetime.now().date()
    today_s = today.isoformat()
    rows = conn.execute(
        "SELECT id,event_date,published_at FROM event").fetchall()
    dele = []
    for r in rows:
        ev = parse_date(r["event_date"])
        expired = False
        if ev and ev < today_s:
            expired = True                     # 仅活动日期已过才删
        elif not ev:
            # 无活动日期：靠发布时间 TTL（不看 signup_deadline，避免误删已报名活动）
            pub = parse_date(r["published_at"])
            if pub and (today - datetime.strptime(pub, "%Y-%m-%d").date()).days > undated_days:
                expired = True
        if expired:
            dele.append(r["id"])
    for i in dele:
        conn.execute("DELETE FROM event WHERE id=?", (i,))
    conn.commit()
    return len(dele)


# 外域政务服务/信访/考试等挂件，非本区活动，直接丢弃
NOISE_DOMAINS = ("zjzwfw.gov.cn", "zxts.zj.gov.cn", "liuyan.www.gov.cn",
                 "gwy.zjks.com", "zjks.com", "zj.gov.cn/col", "www.gov.cn")


def crawl_portal(sess, base, district, source, limit_days, seen_cols, recs):
    """抓一个门户：首页 + JPAAS 栏目 + 静态链接 + 跟进栏目页（1 层）。"""
    try:
        r = sess.get(base, timeout=20)
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
    except Exception as e:
        print("  portal err %s: %s" % (base, e))
        return
    cutoff = (datetime.now() - timedelta(days=limit_days)).strftime("%Y-%m-%d")

    # 1) JPAAS：首页所有 queryData -> build/unit -> 解析
    qds = extract_query_datas(html)
    rendered = html
    for qd in qds:
        uh = jpaas_unit(base, qd, sess)
        if uh:
            rendered += "\n" + uh

    # 2) 解析首页（静态+渲染）里的链接
    links = parse_links(rendered, base)
    col_urls = set()
    for t, h, near in links:
        if any(d in h for d in NOISE_DOMAINS):
            continue
        if re.search(r"/col/col\d+/index\.html", h):
            col_urls.add(h.split("?")[0])
        if re.search(r"/art/|/content/|/news/|/_info/|/detail", h, re.I) and "index.html" not in h:
            _add_article(t, h, near, base, district, source, "", cutoff, recs)

    # 3) 跟进栏目页（限 1 层，最多 20 个，去重）
    new_cols = [c for c in col_urls if c not in seen_cols]
    seen_cols.update(col_urls)
    for cu in new_cols[:20]:
        try:
            rc = sess.get(cu, timeout=15)
            rc.encoding = rc.apparent_encoding or "utf-8"
            chtml = rc.text
        except Exception:
            continue
        col_name = ""
        m = re.search(r"<title>([^<]{2,40})", chtml)
        if m:
            col_name = clean_title(m.group(1)).split("-")[0].split("_")[0].strip()
        crendered = chtml
        for qd in extract_query_datas(chtml):
            uh = jpaas_unit(base, qd, sess)
            if uh:
                crendered += "\n" + uh
        for t, h, near in parse_links(crendered, base):
            if any(d in h for d in NOISE_DOMAINS):
                continue
            if re.search(r"/art/|/content/|/news/|/_info/|/detail", h, re.I) and "index.html" not in h:
                _add_article(t, h, near, base, district, source, col_name, cutoff, recs)


def _add_article(title, url, near, base, district, source, col, cutoff, recs):
    if not is_joinable(title):
        return
    pub = extract_date(near, url)
    if pub and pub < cutoff:
        return
    ev, loc, sg = extract_fields((near or "") + " " + title)   # 从列表邻近文本抽活动时间/地点/报名
    if not ev:
        ev = guess_event_date(near, pub)
    recs.append(_rec(title, url, source, "", district, col, pub, title, ev, loc, sg))


# ----------------------- 搜狗微信 -----------------------
def sogou(sess, query, district, limit_days):
    out = []
    try:
        r = sess.get("https://weixin.sogou.com/weixin", params={"type": 2, "query": query}, timeout=20)
    except Exception as e:
        print("  sogou err:", query, e)
        return out, False
    if r.status_code != 200 or "antispider" in r.text or "请输入验证码" in r.text:
        return out, True
    soup = BeautifulSoup(r.text, "html.parser")
    cutoff = datetime.now() - timedelta(days=limit_days)
    for li in soup.select("li"):
        a = li.select_one(".txt-box h3 a")
        if not a:
            continue
        title = clean_title(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        if href.startswith("/"):
            href = "https://weixin.sogou.com" + href
        mt = re.search(r"timeConvert\('(\d+)'\)", str(li))
        pub = ""
        if mt:
            try:
                dt = datetime.fromtimestamp(int(mt.group(1)))
                if dt < cutoff:
                    continue
                pub = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        acct_el = li.select_one(".all-time-y2") or li.select_one(".account")
        acct = acct_el.get_text(strip=True) if acct_el else ""
        txt = li.get_text(" ", strip=True)
        if not is_joinable(title, txt):
            continue
        ev, loc, signup = extract_fields(txt)
        out.append(_rec(title, href, "公众号", acct or query, district, "", pub, txt, ev, loc, signup))
    return out, False


def enrich_details(sess, limit=60):
    """补全缺活动时间/地点的报名类条目的原文信息（活动时间/地点/报名/摘要）。"""
    conn = db()
    rows = conn.execute(
        "SELECT id,url,title FROM event WHERE (event_date IS NULL OR event_date='') "
        "AND url LIKE 'http%' AND (title LIKE '%报名%' OR title LIKE '%招募%' OR title LIKE '%活动%' "
        "OR title LIKE '%亲子%' OR title LIKE '%市集%' OR title LIKE '%展览%' OR title LIKE '%演出%' "
        "OR title LIKE '%游园%' OR title LIKE '%开放日%' OR title LIKE '%讲座%' OR title LIKE '%比赛%' "
        "OR title LIKE '%夏令营%' OR title LIKE '%体验%' OR title LIKE '%文明实践%' OR title LIKE '%志愿服务%' "
        "OR title LIKE '%义诊%' OR title LIKE '%嘉年华%' OR title LIKE '%庙会%' OR title LIKE '%灯会%') "
        "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    done = 0
    for r in rows:
        try:
            rr = sess.get(r["url"], timeout=15)
            rr.encoding = rr.apparent_encoding or "utf-8"
            soup = BeautifulSoup(rr.text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            ev, loc, signup = extract_fields(text)
            summary = text[:200]
            conn.execute(
                "UPDATE event SET event_date=?, location=?, signup_method=?, summary=? WHERE id=?",
                (ev, loc, signup, summary, r["id"]))
            done += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["gov", "sogou", "bdb", "enrich", "expire", "all"], default="all")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--enrich-limit", type=int, default=60)
    ap.add_argument("--delay", type=float, default=14.0, help="搜狗每查询间隔秒数")
    args = ap.parse_args()
    ensure_schema()
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    conn = db()
    n = 0
    seen_cols = set()

    if args.only in ("gov", "all"):
        for district, base, src in PORTALS:
            recs = []
            crawl_portal(sess, base, district, src, args.days, seen_cols, recs)
            for it in recs:
                if upsert(conn, it):
                    n += 1
            conn.commit()
            print("gov   | %-6s %-12s -> %d 条" % (district, src, len(recs)))

    if args.only in ("sogou", "all"):
        captcha = 0
        got = 0
        for qy, dist in SOGOU_QUERIES:
            items, cap = sogou(sess, qy, dist, args.days)
            for it in items:
                if upsert(conn, it):
                    n += 1
                    got += 1
            conn.commit()
            print("sogou | %-22s -> %d%s" % (qy, len(items), " [captcha]" if cap else ""))
            if cap:
                captcha += 1
                if captcha >= 4:
                    print("  captcha 连续 %d 次，本轮停止" % captcha)
                    break
                time.sleep(120)      # 被拦就冷却 2 分钟再继续
            else:
                time.sleep(args.delay)
        print("sogou 本轮新增 %d，累计 total=%d" % (got, conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]))

    if args.only in ("enrich", "all"):
        done = enrich_details(sess, args.enrich_limit)
        print("enrich| 补全详情 %d 条" % done)

    removed = expire_sweep(conn)
    if removed:
        print("清理过期活动: %d 条" % removed)
    tot = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    print("DONE upserts=%d total=%d" % (n, tot))
    conn.close()


if __name__ == "__main__":
    main()
