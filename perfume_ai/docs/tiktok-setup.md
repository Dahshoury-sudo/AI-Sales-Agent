# Connecting TikTok DMs

With this set up, the agent answers TikTok direct messages for a store, the same way it answers
Messenger and Instagram DMs. Comments on TikTok videos are not handled yet.

TikTok works differently from Meta in three ways:

- **One TikTok app for all stores.** WebVitas owns a single TikTok developer app. Each store
  connects its own TikTok account by pressing **ربط تيك توك** in the dashboard and approving on
  TikTok's page. Store owners never paste tokens.
- **Tokens expire daily.** Access tokens last about 24 hours and are refreshed automatically
  (hourly beat task + before each send). The refresh token lasts about 30 days. If refreshing
  doesn't extend it, the owner gets a dashboard notification 3 days before it runs out and must
  press **ربط تيك توك** again.
- **48-hour window.** TikTok accepts replies only within 48 hours of the customer's last message,
  and only after the customer has written first. Nothing extends the window the way Meta's
  HUMAN_AGENT tag does. A staff reply after 48 hours is refused with an explanation.

> **Unverified API details.** TikTok's developer docs load only in a browser and could not be
> read while this was built. The endpoints and field names in
> `products/services/tiktok_service.py` come from Chatwoot's open-source TikTok integration and
> other integrators' docs. Check them against the portal when the app is approved, and do the
> first-message check in step 6.

## 1. Requirements

- **The store's TikTok account is a Business account.** Personal and Creator accounts can't use
  the messaging API. TikTok app → Profile → ☰ → Settings and privacy → Account → **Switch to
  Business Account**.
- **Region.** TikTok doesn't offer the Business Messaging API for accounts registered in the EEA,
  Switzerland or the UK. US accounts need extra data-security review. No source mentions Egypt
  either way, so confirm it when applying (step 2).

## 2. Create the TikTok app (once, by WebVitas)

1. Sign in at **business-api.tiktok.com** (TikTok API for Business) and create a developer
   account.
2. **My Apps → Create app.** Name it after WebVitas, not after a store.
3. **Apply for Business Messaging API access.** TikTok reviews this; it can take days or longer.
   Request these scopes:

   | Scope | Used for |
   |---|---|
   | `user.info.basic`, `user.info.username`, `user.info.profile` | Showing which account is connected |
   | `user.account.type` | Confirming it is a Business account |
   | `message.list.read` | Receiving DMs (required) |
   | `message.list.send` | Replying (required) |
   | `message.list.manage` | Managing conversations |

   The dashboard refuses a connection that comes back without `message.list.read` and
   `message.list.send`.
4. **Redirect URI:** `https://<your-domain>/dashboard/tiktok/callback/`. It must match
   `TIKTOK_REDIRECT_URI` exactly, including the trailing slash.
5. Note the app's **App ID** (also used as the OAuth `client_key`) and **Secret**.

**One webhook URL per app.** Staging and production each need their own TikTok app, or staging
will receive production's messages.

## 3. Server settings

Set these in the environment (`.env` / Railway variables):

| Variable | Value |
|---|---|
| `TIKTOK_APP_ID` | The app's App ID |
| `TIKTOK_APP_SECRET` | The app's Secret. It also verifies every webhook. |
| `TIKTOK_REDIRECT_URI` | `https://<your-domain>/dashboard/tiktok/callback/` |
| `TIKTOK_API_BASE` | Optional. Default `https://business-api.tiktok.com/open_api/v1.3` |
| `TIKTOK_AUTH_URL` | Optional. Default `https://www.tiktok.com/v2/auth/authorize` |
| `TIKTOK_WEBHOOK_MAX_AGE` | Optional, seconds. `0` (default) skips the timestamp check; see step 6. |

Celery beat must be running: the `refresh-tiktok-tokens` entry refreshes tokens every hour.

## 4. Register the webhook

Once the app is approved and the server is deployed:

```
python manage.py tiktok_register_webhook https://<your-domain>/api/webhooks/tiktok/
```

This calls TikTok's `business/webhook/update/` for the `DIRECT_MESSAGE` event. If the portal
has its own webhook field instead, put the same URL there.

## 5. Connect a store

1. Dashboard → **ربط المنصات** → **تيك توك** card → **ربط تيك توك**.
2. Log in with the store's TikTok Business account and approve.
3. You land back on the dashboard with "تم ربط تيك توك ✅" and the card shows `@username`.
4. The **رسائل تيك توك** switch under الردود التلقائية turns the bot's TikTok replies on or off.
   Messages are still saved when it's off.

One TikTok account can be connected to one store only.

## 6. First live message: check the payload

The webhook parser was written without a real TikTok payload, so do this check once:

1. From a second TikTok account (not the store's), send the store a DM.
2. Check the server log:
   - `TikTok webhook signature valid, age Ns` means the signature format is right.
   - `Rejecting TikTok webhook: bad or missing signature` means compare the real
     `Tiktok-Signature` header with `verify_signature` in `tiktok_service.py`.
   - `TikTok im_receive_msg webhook not understood; payload keys [...]` means the payload shape
     differs. Fix `parse_webhook` and the fixtures at the top of `products/test_tiktok.py`
     together.
   - `No store connected to TikTok business X` means the parser found a business id that
     doesn't match the one saved at connection time. Look at which payload field carries it.
3. The bot's reply should arrive in the TikTok app. If it doesn't, look for
   `TikTok ... refused` in the log; the `code` and `message` say why.
4. Watch whether a retried webhook keeps its original signature timestamp (compare the logged
   ages). If retries are re-signed, you can set `TIKTOK_WEBHOOK_MAX_AGE=300`.
5. Send an image and check the inbox shows "📎 مرفق من العميل" and the bot answers.
6. From the handoff inbox, send a staff reply within 48 hours and confirm it arrives.

## Not done yet

- Comments on TikTok videos. This is waiting on TikTok's comment API docs (endpoint, 48-hour
  rule, and whether new comments arrive by webhook or must be polled).
- Viewing customers' images in the inbox. They're stored as `tiktok-media:<id>` until media
  download is added.
- Caching the uploaded bottle image: each bottle-image reply uploads it again (3 API calls).
