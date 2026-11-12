# Mail Desk - Zoho Mail + Claude

Reads new Zoho inbox mails, classifies them (rate inquiry / pullout order / other), extracts dimensions, weight,
pickup and delivery, flags missing details, drafts a reply, and waits for your approval. Nothing sends without
your tap, and a draft containing [RATE] cannot be sent until you type the price.

## 1. Zoho setup (about 10 min)
1. Open https://api-console.zoho.com (use your region: .sa / .eu / .in) > Add Client > **Self Client**.
2. Note the Client ID and Client Secret.
3. Generate Code tab. Scope: `ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.ALL`, duration 10 min. Copy the code.
4. Exchange it once for a refresh token:
   `curl -X POST "https://accounts.zoho.com/oauth/v2/token" -d "grant_type=authorization_code&client_id=ID&client_secret=SECRET&code=CODE"`
   Save the `refresh_token`.
5. Get your account id: `curl -H "Authorization: Zoho-oauthtoken ACCESS_TOKEN" https://mail.zoho.com/api/accounts` and copy `accountId`.

## 2. Run
    pip install -r requirements.txt
    cp .env.example .env   # fill in, then: export $(cat .env | xargs)
    python app.py          # open http://localhost:8000  (user: admin)

## 3. Deploy (phone access)
Push to GitHub, deploy on Render or Railway: build `pip install -r requirements.txt`, start `gunicorn -w 1 app:app`
(keep ONE worker). Add the .env values as environment variables and a small persistent disk for DB_PATH.

## Notes
- Written without access to your Zoho account, so test with one mail first; if an endpoint errors, check the
  Zoho Mail API docs for your region and adjust the path in `app.py`.
- Replies are sent as a new "Re:" mail (not threaded). Pullout-order drafts are basic for now.

## Learning your style
On start it reads your **Sent** folder (up to 600 mails, 30 per minute in the background), writes a style guide
about how you greet, ask, quote and close, and saves it. For every new mail it also finds your most similar past
replies and shows them to Claude, so drafts copy your real wording. Past rates for similar jobs appear under the
draft as a hint, but the price is still yours to type. Tap "relearn my style" on the dashboard to refresh it.
Add scope `ZohoMail.messages.READ` is already covered by `ZohoMail.messages.ALL`.
