import json, time, os, requests, re
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

def candidate(u):
    return u and u.startswith("http") and "news.google.com" not in u and "google.com" not in u

for pid, url, title in pending:
    final = url
    okf = False
    note = ""
    diag = [f"TITLE: {title}\nORIG: {url}"]
    try:
        # follow redirect to reader page
        rg = requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)
        landed = rg.url
        diag.append(f"landed: {landed}\nreader status={rg.status_code} len={len(rg.text)}")
        soup = BeautifulSoup(rg.text, "html.parser")
        cands = []
        # canonical
        can = soup.find("link", rel="canonical")
        if can and can.get("href"):
            cands.append(("canonical", can["href"]))
        # amp
        amp = soup.find("link", rel="amphtml")
        if amp and amp.get("href"):
            cands.append(("amphtml", amp["href"]))
        # any external a href
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if candidate(h):
                cands.append(("a", h))
        # meta / data-url / iframe src
        for tag in soup.find_all(True):
            for attr in ("href", "src", "data-url", "content"):
                v = tag.get(attr)
                if isinstance(v, str) and candidate(v):
                    cands.append((attr, v))
        # scan raw text for http urls
        for m in re.finditer(r'https?://[^\s"\'<>]+', rg.text):
            u = m.group(0)
            if candidate(u):
                cands.append(("raw", u))
        seen = set(); uniq = []
        for k, u in cands:
            if u not in seen:
                seen.add(u); uniq.append((k, u))
        diag.append(f"CANDIDATES ({len(uniq)}):")
        for k, u in uniq[:20]:
            diag.append(f"  {k}: {u[:200]}")
        if uniq:
            final = uniq[0][1]; okf = True; note = "reader_" + uniq[0][0]
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
