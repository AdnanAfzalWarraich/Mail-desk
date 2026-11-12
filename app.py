import os, re, json, time, sqlite3, threading, html
import requests
from flask import Flask, request, redirect, Response
from anthropic import Anthropic

E = os.environ.get
ACC, MAIL, AID = E("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com"), E("ZOHO_MAIL_URL", "https://mail.zoho.com"), E("ZOHO_ACCOUNT_ID")
MY, MODEL, PW = E("MY_EMAIL"), E("MODEL", "claude-sonnet-5-5"), E("DASHBOARD_PASSWORD", "change-me")
DB = E("DB_PATH", "mail.db")
ai = Anthropic()
app = Flask(__name__)
_tok = {"v": None, "exp": 0}

SYSTEM = """You assist Adnan Afzal Warraich at Demas Al Arabia, a transport company (lowbed, flatbed, containers, port pullouts) in Saudi Arabia.
Read ONE incoming email. Return ONLY JSON:
{"kind":"rate_inquiry|pullout_order|other",
 "summary":"2 short lines",
 "fields":{"equipment":"","cargo":"","length_m":"","width_m":"","height_m":"","weight_tons":"","pickup":"","delivery":"","container_numbers":"","port":"","bayan_attached":"","pod_attached":""},
 "missing":["fields that are required but absent or unclear"],
 "rate_note":"rates you saw in the past examples for similar jobs (route, equipment, rate), else empty",
 "draft":"reply email body"}
Rules: For rate_inquiry, dimensions (m), weight (tons), pickup and delivery are REQUIRED every time; if any is missing, the draft politely asks for it. Never invent a rate: write [RATE] where the price goes. For pullout_order, list container numbers, port, and whether Bayan/POD is attached; draft a short confirmation. Leave unknown fields empty. Reply in the customer's language. Write EXACTLY like Adnan's past emails given below: same greeting, wording, length, closing and signature. Plain text, no emojis, no markdown, nothing that sounds like an AI. Follow the way he handles similar jobs in the examples (what he asks, how he quotes, how he confirms)."""

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    with db() as c:
        c.execute("""create table if not exists mails(id text primary key, sender text, subject text, body text,
        kind text, data text, draft text, status text, ts real)""")
        c.execute("create table if not exists sent(id text primary key, recipient text, subject text, body text, ts real)")
        c.execute("create table if not exists kv(k text primary key, v text)")

def token():
    if time.time() < _tok["exp"]: return _tok["v"]
    r = requests.post(f"{ACC}/oauth/v2/token", params={"refresh_token": E("ZOHO_REFRESH_TOKEN"), "client_id": E("ZOHO_CLIENT_ID"),
        "client_secret": E("ZOHO_CLIENT_SECRET"), "grant_type": "refresh_token"}, timeout=30).json()
    _tok.update(v=r["access_token"], exp=time.time() + 3000); return _tok["v"]

def zoho(method, path, **kw):
    r = requests.request(method, f"{MAIL}/api/accounts/{AID}{path}", headers={"Authorization": f"Zoho-oauthtoken {token()}"}, timeout=60, **kw)
    r.raise_for_status(); return r.json().get("data")

def strip(h): return re.sub(r"\s+\n", "\n", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>", "\n", h or ""))))[:8000]

def kv(k, v=None):
    with db() as c:
        if v is None:
            r = c.execute("select v from kv where k=?", (k,)).fetchone(); return r["v"] if r else None
        c.execute("insert or replace into kv values(?,?)", (k, str(v)))

def own(t): return re.split(r"(?im)^(?:on .{5,80}wrote:|from:|-{2,}\s*original|>|\*?from:\*?)", t)[0].strip()

def sync_sent():
    fid = next(f["folderId"] for f in zoho("GET", "/folders") if f.get("folderType") == "Sent")
    start, bf = int(kv("bf") or 1), kv("bf_done") != "1"
    for st in ([1] + ([start] if bf else [])):
        msgs = zoho("GET", "/messages/view", params={"folderId": fid, "start": st, "limit": 30}) or []
        for m in msgs:
            mid = str(m["messageId"])
            with db() as c:
                if c.execute("select 1 from sent where id=?", (mid,)).fetchone(): continue
            body = strip((zoho("GET", f"/folders/{fid}/messages/{mid}/content") or {}).get("content"))[:3500]
            with db() as c:
                c.execute("insert into sent values(?,?,?,?,?)", (mid, m.get("toAddress", ""), m.get("subject", ""), body, time.time()))
            time.sleep(0.3)
        if bf and st == start:
            if len(msgs) < 30 or start >= int(E("LEARN_MAX", "600")): kv("bf_done", "1")
            else: kv("bf", start + 30)

def words(t): return set(re.findall(r"\w{4,}", t.lower()))

def similar(subject, body, n=4):
    q = words(subject + " " + body); rows = []
    for r in db().execute("select subject, body from sent"):
        rows.append((len(q & words(r["subject"] + " " + r["body"])), r))
    rows.sort(key=lambda x: -x[0])
    return [r for sc, r in rows[:n] if sc > 2]

def learn_style(force=False):
    n = db().execute("select count(*) from sent").fetchone()[0]
    if n < 15 or not (force or not kv("style") or n - int(kv("style_n") or 0) >= 100): return
    rows = [own(r["body"]) for r in db().execute("select body from sent order by ts desc limit 120")]
    sample = "\n---\n".join(t[:600] for t in rows if len(t) > 40)[:30000]
    m = ai.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content":
        "These are emails written by Adnan at a transport company. Write a short guide to imitate him exactly: greeting and closing, "
        "tone, typical length, language mix, favourite phrases, how he asks for details, how he quotes rates and confirms jobs, "
        "and how he handles lowbed, flatbed, container and port pullout requests. Be concrete, quote real phrases.\n\n" + sample}])
    kv("style", m.content[0].text); kv("style_n", n)

def analyse(sender, subject, body):
    ex = "\n\n=====\n".join(f"Subject: {r['subject']}\n{r['body'][:1800]}" for r in similar(subject, body))
    sys = SYSTEM + "\n\nHIS STYLE GUIDE:\n" + (kv("style") or "(not learned yet)") + "\n\nHIS PAST SENT EMAILS FOR SIMILAR JOBS:\n" + (ex or "(none yet)")
    m = ai.messages.create(model=MODEL, max_tokens=1500, system=sys,
        messages=[{"role": "user", "content": f"From: {sender}\nSubject: {subject}\n\n{body}"}])
    t = re.sub(r"^```(?:json)?|```$", "", m.content[0].text.strip()).strip()
    return json.loads(t)

def poll():
    while True:
        try:
            sync_sent(); learn_style()
        except Exception as e:
            print("learn error:", e, flush=True)
        try:
            inbox = next(f["folderId"] for f in zoho("GET", "/folders") if f.get("folderType") == "Inbox")
            for m in zoho("GET", "/messages/view", params={"folderId": inbox, "status": "unread", "limit": 25}) or []:
                mid = str(m["messageId"])
                with db() as c:
                    if c.execute("select 1 from mails where id=?", (mid,)).fetchone(): continue
                body = strip((zoho("GET", f"/folders/{inbox}/messages/{mid}/content") or {}).get("content"))
                sender = m.get("fromAddress", "")
                r = analyse(sender, m.get("subject", ""), body)
                with db() as c:
                    c.execute("insert into mails values(?,?,?,?,?,?,?,?,?)", (mid, sender, m.get("subject", ""), body, r["kind"],
                        json.dumps({"fields": r["fields"], "missing": r["missing"], "summary": r["summary"], "rate_note": r.get("rate_note", "")}), r["draft"], "pending", time.time()))
        except Exception as e:
            print("poll error:", e, flush=True)
        time.sleep(int(E("POLL_SECONDS", "60")))

def authed():
    a = request.authorization
    return a and a.password == PW

def guard():
    return Response("Login", 401, {"WWW-Authenticate": 'Basic realm="Mail Desk"'})

PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Mail Desk</title>
<style>body{font:15px system-ui;margin:0;background:#f4f5f7;color:#111}h1{font-size:18px;padding:14px 16px;margin:0;background:#12355b;color:#fff}
.c{background:#fff;margin:12px;padding:14px;border-radius:10px;box-shadow:0 1px 3px #0002}.b{font-size:12px;padding:2px 8px;border-radius:9px;background:#e3ecf7}
table{width:100%;border-collapse:collapse;margin:8px 0}td{padding:3px 4px;border-bottom:1px solid #eee;font-size:14px}td:first-child{color:#666;width:42%}
.m{color:#b00020;font-weight:600}textarea{width:100%;height:170px;box-sizing:border-box;font:14px system-ui;padding:8px}
button{padding:10px 18px;border:0;border-radius:8px;font-size:15px;margin-right:8px}.s{background:#12355b;color:#fff}.k{background:#ddd}</style>
<h1>Mail Desk &mdash; {N} to review</h1>{C}"""

@app.route("/")
def home():
    if not authed(): return guard()
    cards = ""
    for r in db().execute("select * from mails where status='pending' order by ts desc"):
        d = json.loads(r["data"]); f = d["fields"]
        rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in f.items() if v)
        miss = f"<p class=m>CHECK: {html.escape(', '.join(d['missing']))}</p>" if d["missing"] else ""
        rn = f"<p><small>Past rates: {html.escape(d['rate_note'])}</small></p>" if d.get("rate_note") else ""
        cards += f"""<div class=c><span class=b>{html.escape(r['kind'])}</span> <b>{html.escape(r['subject'])}</b><br><small>{html.escape(r['sender'])}</small>
<p>{html.escape(d['summary'])}</p><table>{rows}</table>{miss}{rn}
<form method=post action=/send/{r['id']}><textarea name=draft>{html.escape(r['draft'])}</textarea><p>
<button class=s>Approve &amp; send</button><button class=k formaction=/skip/{r['id']}>Skip</button></p></form></div>"""
    n = cards.count("class=c>")
    k = db().execute("select count(*) from sent").fetchone()[0]
    foot = f"<p style='padding:0 16px;color:#666'>Learned from {k} sent mails &middot; <a href=/relearn>relearn my style</a></p>"
    return PAGE.replace("{N}", str(n)).replace("{C}", (cards or "<p style='padding:16px'>Nothing waiting.</p>") + foot)

@app.route("/relearn")
def relearn():
    if not authed(): return guard()
    learn_style(force=True); return redirect("/")

@app.route("/send/<mid>", methods=["POST"])
def send(mid):
    if not authed(): return guard()
    draft = request.form["draft"]
    if "[RATE]" in draft: return "Replace [RATE] with the price before sending. Go back.", 400
    r = db().execute("select * from mails where id=?", (mid,)).fetchone()
    to = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", r["sender"]).group(0)
    subj = r["subject"] if r["subject"].lower().startswith("re:") else "Re: " + r["subject"]
    zoho("POST", "/messages", json={"fromAddress": MY, "toAddress": to, "subject": subj, "content": draft, "mailFormat": "plaintext"})
    with db() as c: c.execute("update mails set status='sent', draft=? where id=?", (draft, mid))
    return redirect("/")

@app.route("/skip/<mid>", methods=["POST"])
def skip(mid):
    if not authed(): return guard()
    with db() as c: c.execute("update mails set status='skipped' where id=?", (mid,))
    return redirect("/")

init()
threading.Thread(target=poll, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(E("PORT", "8000")))
