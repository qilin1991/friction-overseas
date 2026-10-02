import json, time, requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# 读取待处理: gnews_pending.txt 每行 id|url|title
pending = []
with open("gnews_pending.txt", encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("|", 2)
        pid = parts[0]
        url = parts[1] if len(parts) > 1 else ""
        title = parts[2] if len(parts) > 2 else ""
        pending.append((pid, url, title))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
out = open("gnews_resolved.jsonl", "w", encoding="utf-8")
ok = fail = 0

for pid, url, title in pending:
    final = url
    okf = False
    note = ""
    try:
        if title:
            q = "https://www.bing.com/news/search?q=" + requests.utils.quote(title) + "&format=rss&setmkt=en-US"
            r = requests.get(q, headers={"User-Agent": UA}, timeout=25)
            # 1) 优先 RSS XML 的 <url> 真实直链
            try:
                root = ET.fromstring(r.content)
                for it in root.iter("item"):
                    u = (it.findtext("url") or it.findtext("link") or "").strip()
                    if u and u.startswith("http") and "news.google.com" not in u and "bing.com" not in u:
                        final = u
                        okf = True
                        note = "bing_rss_url"
                        break
            except Exception as e:
                note = "rss_parse_fail:" + str(e)[:40]
            # 2) 退化: HTML 里的 apiclick 解码 / data-url / 真实 a 标签
            if not okf:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    h = a["href"]
                    if h.startswith("http") and "news.google.com" not in h and "bing.com" not in h and "microsoft" not in h and "msn.com" not in h:
                        final = h
                        okf = True
                        note = "bing_html_a"
                        break
                if not okf:
                    for tag in soup.find_all(attrs={"data-url": True}):
                        u = tag["data-url"]
                        if u.startswith("http") and "bing.com" not in u and "news.google.com" not in u:
                            final = u
                            okf = True
                            note = "bing_html_dataurl"
                            break
        if okf:
            ok += 1
        else:
            fail += 1
            note = note or "no_title_or_unresolved"
    except Exception as e:
        fail += 1
        note = "err:" + str(e)[:50]
    out.write(json.dumps({"id": pid, "orig": url, "final": final, "ok": okf, "note": note}, ensure_ascii=False) + "\n")
    time.sleep(0.4)

out.close()
print("DONE total=%d ok=%d fail=%d" % (len(pending), ok, fail))
