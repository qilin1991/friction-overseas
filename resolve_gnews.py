import json, time, os, requests, base64, re
from bs4 import BeautifulSoup

os.makedirs("out", exist_ok=True)

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

STOP = set("the a an of to on in for and or with is are was were be by at from as it its new".split())

def words(s):
    return set(w for w in re.findall(r"[a-zA-Z0-9]+", s.lower()) if w not in STOP and len(w) > 2)

def score(title, text):
    tw = words(title); rw = words(text)
    if not tw:
        return 0.0
    return len(tw & rw) / len(tw)

def clean(u):
    return u and u.startswith("http") and "news.google.com" not in u and "bing.com/ck" not in u and "google.com" not in u and "bing.com" not in u and "msockid" not in u

def decode_ck_a(href):
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return None
    s = m.group(1) + "=" * (-len(m.group(1)) % 4)
    s = s.replace("-", "+").replace("_", "/")
    try:
        raw = base64.b64decode(s)
        i = raw.find(b"http")
        if i >= 0:
            u = raw[i:].split(b"\x00")[0].split(b'"')[0].split(b"'")[0].split(b" ")[0]
            return u.decode("latin1")
    except Exception:
        pass
    return None

def bing_search(title, quoted):
    q = ('"' + title + '"') if quoted else title
    url = "https://www.bing.com/search?q=" + requests.utils.quote(q)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")
    res = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        h = a.get("href", "")
        htxt = a.get_text(" ", strip=True)
        snip = li.get_text(" ", strip=True)
        if "bing.com/ck/a" in h:
            u = decode_ck_a(h)
        elif h.startswith("http"):
            u = h
        else:
            u = None
        if u and clean(u):
            sc = max(score(title, htxt), score(title, snip))
            res.append((u, htxt, sc))
    return r.status_code, res

for pid, url, title in pending:
    final = url
    okf = False
    note = ""
    diag = [f"TITLE: {title}"]
    best = None
    try:
        for quoted in (True, False):
            st, res = bing_search(title, quoted)
            diag.append(f"[{'quoted' if quoted else 'plain'}] status={st} organic={len(res)}")
            for u, t, sc in res[:8]:
                diag.append(f"   sc={sc:.2f} {u[:130]} | {t[:60]}")
                if best is None or sc > best[0]:
                    best = (sc, u, "quoted" if quoted else "plain")
        if best and best[0] >= 0.34:
            final = best[1]; okf = True; note = f"bing_match_{best[2]}_sc{best[0]:.2f}"
        else:
            diag.append("NO good match (best sc=%.2f)" % (best[0] if best else 0))
        with open(f"out/diag_{pid}.txt", "w", encoding="utf-8") as d:
            d.write("\n".join(diag) + "\n")
        if okf:
            ok += 1
        else:
            fail += 1
            note = note or "unresolved"
    except Exception as e:
        fail += 1
        note = "err:" + str(e)[:50]
        with open(f"out/diag_{pid}.txt", "w", encoding="utf-8") as d:
            d.write("\n".join(diag) + "\nERR:" + str(e) + "\n")
    out.write(json.dumps({"id": pid, "orig": url, "final": final, "ok": okf, "note": note}, ensure_ascii=False) + "\n")
    time.sleep(0.4)

out.close()
print("DONE total=%d ok=%d fail=%d" % (len(pending), ok, fail))
