# Connecting a Facebook Page and Instagram account

With this connection set up, the agent answers Messenger DMs, Instagram DMs, Facebook comments and
Instagram comments for a store. It uses the store's own Meta app (Facebook Login and a Page token),
so every call goes to `graph.facebook.com`.

Meta's dashboard is reorganised often, so menu names below may differ slightly. The dashboard check
at the end tells you exactly what is still wrong, so you don't need to get every step right first time.

## 1. Before you start

- **Instagram is a Professional account** (Business or Creator), linked to the Facebook Page:
  Page → Settings → Linked accounts → Instagram.
- **Instagram allows apps to read messages**: in the Instagram app, go to Settings →
  Messages and story replies → Message controls → Connected tools → turn on **Allow access to messages**.
- **You have full control of the Page** (it shows under your Facebook account's Pages) and an
  **Admin or Developer role on the Meta app**.
- **The agent is deployed** on a public HTTPS URL (Railway).

## 2. Meta app: permissions

In the app (developers.facebook.com → My Apps → your app), open the Messenger and Instagram use
cases and add these permissions:

| Permission | Used for |
|---|---|
| `pages_messaging` | Messenger DMs, and private replies to comments |
| `pages_manage_metadata` | Subscribing the Page to the webhook |
| `pages_read_engagement` | Reading posts for context, and the post-mute picker |
| `pages_manage_engagement` | Public replies to Facebook comments |
| `pages_show_list` | Choosing the Page when generating the token |
| `instagram_basic` | Reading the Instagram account and media |
| `instagram_manage_messages` | Instagram DMs, and private replies to Instagram comments |
| `instagram_manage_comments` | Instagram comment webhooks and public replies |
| `business_management` | Only if the Page belongs to a Business portfolio |

**Access levels:**
- Messenger on your own Page works without App Review.
- Instagram comments and messages from people who have **no role on the app** need **Advanced
  Access**, which requires App Review.
- Until that review is done, test Instagram with an account that has a role on the app (step 6
  tells you which case you're in).

## 3. Page token

Go to Messenger use case → **Messenger API Settings** → **Generate access tokens**. Connect the
Page and press **Generate**, then copy the token.
- It must be a **Page** token that doesn't expire. The dashboard check confirms both.
- If you have to make one by hand instead:
  1. Graph API Explorer: user token with the permissions above.
  2. Access Token Debugger: **Extend Access Token**.
  3. `GET /me/accounts`: copy the Page's `access_token`.

## 4. Webhooks

In the dashboard, go to **ربط المنصات** → **حالة الربط بفيسبوك وانستجرام**:
- Copy the **Callback URL** (`https://<your-host>/api/webhooks/meta/`).
- Press **توليد** next to **Verify Token**, then **حفظ إعدادات المنصات**.

In the Meta app:
- **Messenger API Settings → Configure webhooks**: paste the Callback URL and the same Verify Token,
  then **Verify and save**. Subscribe the Page fields **`messages`** and **`feed`**.
- **Instagram (API setup with Facebook login) → Configure webhooks**: same URL and token. Subscribe
  **`messages`** and **`comments`**.

The dashboard check below also subscribes the Page to the app (`messages`, `feed`) if it isn't
already.

## 5. Dashboard

In **ربط المنصات**:
- **Messenger & Instagram Access Token (Page Token)**: the token from step 3.
- **App Secret**: App settings → Basic → App secret.
- **Facebook Page ID** and **Instagram Account ID**: can stay empty. The check fills them in.
- **الردود التلقائية**: turn on the platforms you want.
- **ردود التعليقات التلقائية**: one public reply per line.

Then press **احفظ وافحص الربط**. Every row should be ✓:

| Row | What it checks |
|---|---|
| توكن الصفحة | It's a Page token, for this Page |
| التطبيق / App Secret | The secret belongs to the token's app (webhook signatures depend on it) |
| مدة التوكن | The token doesn't expire |
| الصلاحيات | Lists any permission from step 2 that's missing |
| حساب انستجرام | The Instagram business account linked to the Page; its ID is saved automatically |
| اشتراك الصفحة في الـ Webhook | The Page receives `messages` and `feed`; fixed automatically |
| رسائل ماسنجر / رسائل انستجرام | The token can open the Page's conversations |
| Verify Token / الردود / رسائل الرد | Local settings |

## 6. Live test

Use a personal account that has **no role on the app**, so you see what customers see. For each
step, watch the Railway web and worker logs.

1. Send the Page a Messenger DM. The agent should reply.
2. Comment on a Facebook post. You should get a public reply on the comment, plus the answer in DM.
3. Send the Instagram account a DM. The agent should reply.
4. Comment on an Instagram post. You should get a public reply, plus the answer in DM.

**If 1–2 work but 3–4 never reach the logs:** Instagram needs Advanced Access. Submit App Review for
`instagram_manage_messages` and `instagram_manage_comments`, with a screencast of the agent replying.
- Privacy policy URL: https://webvitas.com/privacy
- Business verification may be required.

## 7. After it works

Set `META_REQUIRE_WEBHOOK_SIGNATURE=True` on Railway. From then on, a webhook without a valid
signature from the app secret is rejected, even for a store with no secret saved.
