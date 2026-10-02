import json, time, os, requests, base64, re
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

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
    return u and u.startswith("http") and "news.google.com" not in u and "google.com" not in u and "bing.com" not in u

def try_decode_b64(tok):
    # Google News RSS article base64url -> protobuf -> embedded url
    try:
        s = tok
        s += "=" * (-len(s) % 4)
        s = s.replace("-", "+").replace("_", "/")
        raw = base64.b64decode(s)
        # search for http(s) urls in decoded bytes
        for m in re.finditer(rb"https?://[^\x00-\x1f\s\"'<>]+", raw):
            u = m.group(0).decode("latin1")
            if clean(u):
                return u
    except Exception:
        pass
    return ""

for pid, url, title in pending:
    final = url
    okf = False
    note = ""
    try:
        diag = []
        # STRATEGY A: follow google news redirect
        try:
            rg = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
            chain = [h.url for h in rg.history] + [rg.url]
            diag.append("REDIRECT chain:\n" + "\n".join(chain))
            landed = rg.url
            if clean(landed):
                final = landed
                okf = True
                note = "gnews_redirect"
            else:
                diag.append("redirect landed on google, not publisher: " + landed)
        except Exception as e:
            diag.append("REDIRECT ERR: " + str(e)[:80])
        # STRATEGY B: base64 protobuf decode of the token
        if not okf:
            tok = url.split("/articles/")[-1].split("?")[0]
            du = try_decode_b64(tok)
            diag.append("B64 decode -> " + (du or "(none)"))
            if du:
                final = du
                okf = True
                note = "gnews_b64"
        with open(f"out/diag_{pid}.txt", "w", encoding="utf-8") as d:
            d.write("TITLE: " + title + "\nORIG: " + url + "\n")
            d.write("\n".join(diag) + "\n")
        if okf:
            ok += 1
        else:
            fail += 1
            note = note or "unresolved"
    except Exception as e:
        fail += 1
        note = "err:" + str(e)[:50]
    out.write(json.dumps({"id": pid, "orig": url, "final": final, "ok": okf, "note": note}, ensure_ascii=False) + "\n")
    time.sleep(0.4)

out.close()
print("DONE total=%d ok=%d fail=%d" % (len(pending), ok, fail))
