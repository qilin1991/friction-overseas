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

def clean(u):
    return u and u.startswith("http") and "news.google.com" not in u and "bing.com/ck" not in u and "google.com" not in u and "bing.com" not in u

def decode_ck_a(href):
    # bing /ck/a?...&u=a1<base64url>
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return None
    s = m.group(1)
    s += "=" * (-len(s) % 4)
    s = s.replace("-", "+").replace("_", "/")
    try:
        raw = base64.b64decode(s)
        # real url is plaintext after some prefix bytes; find http
        i = raw.find(b"http")
        if i >= 0:
            u = raw[i:].split(b"\x00")[0].split(b'"')[0].split(b"'")[0].split(b" ")[0]
            return u.decode("latin1")
    except Exception:
        pass
    return None

def bing_web(title):
    q = "https://www.bing.com/search?q=" + requests.utils.quote(title)
    r = requests.get(q, headers={"User-Agent": UA}, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")
    res = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "bing.com/ck/a" in h:
            u = decode_ck_a(h)
            if u and clean(u):
                res.append(("bing_ck", u))
        elif h.startswith("http") and "bing.com" not in h and "microsoft" not in h and "msn.com" not in h:
            res.append(("bing_direct", h))
    return r.status_code, len(r.text), res[:5]

def ddg_html(title):
    q = "https://html.duckduckgo.com/html/?q=" + requests.utils.quote(title)
    r = requests.get(q, headers={"User-Agent": UA}, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")
    res = []
    for a in soup.select("a.result__a"):
        h = a.get("href", "")
        if h.startswith("http") and "duckduckgo.com" not in h:
            res.append(("ddg", h))
    return r.status_code, len(r.text), res[:5]

for pid, url, title in pending:
    final = url
    okf = False
    note = ""
    diag = [f"TITLE: {title}"]
    try:
        st, ln, res = bing_web(title)
        diag.append(f"BINGWEB status={st} len={ln} results={len(res)}")
        for k, u in res[:5]:
            diag.append(f"  {k}: {u[:200]}")
        # pick first bing_ck or bing_direct
        for k, u in res:
            if clean(u):
                final = u; okf = True; note = "bing_web_" + k; break
        if not okf:
            st2, ln2, res2 = ddg_html(title)
            diag.append(f"DDG status={st2} len={ln2} results={len(res2)}")
            for k, u in res2[:5]:
                diag.append(f"  {k}: {u[:200]}")
            for k, u in res2:
                if clean(u):
                    final = u; okf = True; note = "ddg"; break
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
