# Connecting a Facebook Page and Instagram account

With this connection set up, the agent answers Messenger DMs, Facebook comments, Instagram DMs and
Instagram comments for a store. It uses the store's own Meta app and a Facebook **Page token**; every
call goes to `graph.facebook.com` (Meta's "Instagram API with Facebook Login").

Meta renames dashboard menus often, so a label below may differ slightly. The dashboard check in
step 7 tells you exactly what is still wrong, so you don't need every step perfect first time.

## What works when

**The app must be Live** (step 3). In Development mode Meta only sends events from people with a
role on the app (admins, developers, testers), so the agent answers your own account and nobody
else. That is the most common reason "it only replies to me".

Meta's docs also say customers' events need **Advanced Access**, which takes Business Verification +
App Review (step 9):

- Messenger: "Standard Access to receive notifications from people who have a role on your app …
  Advanced Access to receive notifications from your customers … Requires App Review."
- Instagram comments and private replies: Advanced Access is listed as required.

In practice a Live app has received customers' events before review. Run the live test in step 8,
from an account with no role, to see which events already arrive.

## 1. Instagram and the Facebook Page

1. **Instagram is a Professional account.** Instagram app → your profile → ☰ → Settings →
   Account type and tools → **Switch to professional account** → Business.
2. **Instagram is public.** Meta sends no comment webhooks for private accounts.
3. **Instagram is linked to the Page.** On Facebook, switch into the Page → Settings →
   **Linked accounts** → Instagram → **Connect account**, then log in to Instagram.
   (Alternatives: Instagram app → Edit profile → Page → Connect; or Meta Business Suite →
   Settings → Instagram accounts.)
4. **Instagram lets apps answer DMs.** Instagram app → Settings → **Messages and story replies** →
   **Message controls** → **Connected tools** → turn on **Allow access to messages**.
5. **Your Facebook account has full control of the Page** and is an **Admin of the Meta app**.
   Use this same account for every step below.

## 2. Meta app: use cases and permissions

Each permission lives in a use case, and no single use case has them all. The app needs these three:

| Use case | Add with **Customize → Add** |
|---|---|
| **Engage with customers on Messenger from Meta** | `pages_read_engagement`, `instagram_basic`, `instagram_manage_messages` (`pages_messaging`, `pages_manage_metadata`, `pages_show_list`, `business_management` come with it) |
| **Manage everything on your Page** | `pages_manage_engagement` (on by default) |
| **Manage messaging & content on Instagram**, the **API setup with Facebook login** option | `instagram_manage_comments` |

- A use case missing from the app: left menu → **Use cases** → add it. The wizard greys out any use
  case that can't be combined with the ones the app already has.
- What each permission is for:

| Permission | Used for |
|---|---|
| `pages_messaging` | Messenger DMs, and private replies to comments |
| `pages_manage_metadata` | Subscribing the Page to the webhook |
| `pages_read_engagement` | Reading posts for context, and the post-mute picker |
| `pages_manage_engagement` | Public replies to Facebook comments |
| `pages_show_list` | Choosing the Page when generating the token |
| `business_management` | Pages that belong to a Business portfolio |
| `instagram_basic` | Reading the Instagram account and media |
| `instagram_manage_messages` | Instagram DMs, and private replies to Instagram comments |
| `instagram_manage_comments` | Instagram comment webhooks and public replies |

## 3. Meta app: basic settings, then publish

1. **App settings → Basic:**
   - **App icon**: 1024×1024.
   - **Category**: e.g. Business and pages.
   - **Privacy Policy URL**: `https://www.webvitas.com/ar/khosousiya`.
   - **Terms of Service URL**: `https://www.webvitas.com/ar/shorout`.
   - **User data deletion → Data deletion instructions URL**: the privacy URL above (its "الحذف"
     section covers it).
   - Press **Save changes**.
   - Then press **Show** next to **App secret** and keep it for step 7.
2. **Don't skip this:** left menu → **Publish** → **Publish**. Meta sends webhooks for other people only
   while the app is published (Live). The app dashboard's top bar shows the mode (Development / Live).

## 4. Dashboard: Callback URL and Verify Token

In the dashboard, go to **ربط المنصات**:
1. In **حالة الربط بفيسبوك وانستجرام**, copy the **Callback URL** with **نسخ**
   (`https://api.webvitas.com/api/webhooks/meta/`).
2. Press **توليد** next to **Verify Token**.
3. Press **حفظ إعدادات المنصات**. Meta checks the token against the saved one, so save before step 5.

## 5. Meta app: webhooks

Left menu → **Webhooks**. The dropdown at the top picks the object; do both:

1. **Page**:
   1. Press **Subscribe to this object**.
   2. Paste the Callback URL and the Verify Token.
   3. Press **Verify and save**.
   4. In the field list, press **Subscribe** on **`messages`** and **`feed`**. `feed` carries
      Facebook comments.
2. **Instagram**:
   1. Press **Subscribe to this object**.
   2. Paste the same URL and token, then press **Verify and save**.
   3. Press **Subscribe** on **`messages`** and **`comments`**.

Leave the other objects ("User", "Permissions" and so on) alone. The **Test** button next to a field
sends a fake event. With **Page** or **Instagram** selected it comes back 200, but the agent won't
reply to it. Other objects come back 404 because the agent doesn't handle them; that's expected.

Meta also needs the Page itself subscribed to the app. The dashboard check in step 7 does that for you.

## 6. Page token

Use Graph API Explorer, because it lets you tick every permission yourself:

1. Open `https://developers.facebook.com/tools/explorer/`.
2. On the right, under **Meta App**, pick your app.
3. Under **User or Page**, pick **Get User Access Token**.
4. Under **Permissions**, add all nine from step 2.
5. Press **Generate Access Token**. In the Facebook popup:
   - Continue as yourself.
   - Tick the Page, then tick the Instagram account.
   - Press **Save**.
6. Make it last 60 days:
   1. Copy the token.
   2. Open `https://developers.facebook.com/tools/debug/accesstoken/`, paste it and press **Debug**.
   3. Press **Extend Access Token** at the bottom.
   4. Copy the new token.
7. Get the Page token:
   1. Back in Graph API Explorer, paste the new token into **Access Token**.
   2. Query `me/accounts?fields=name,access_token` and press **Submit**.
   3. Copy the `access_token` next to the Page.
8. Optional check: paste it in the Access Token Debugger. It should say **Type: Page** and
   **Expires: Never**. The dashboard check confirms both anyway.

If the Page or a permission isn't offered in the popup:
- **A permission is missing**: the app is missing that permission's use case (step 2).
- **The Page is missing**: you don't have full control of it, or you skipped it in an earlier popup.
  Remove the app under Facebook → Settings & privacy → Settings → **Business integrations**, then
  generate again.

## 7. Dashboard: token, secret, check

In **ربط المنصات**:
- **Messenger & Instagram Access Token (Page Token)**: the token from step 6.
- **App Secret**: from step 3.
- **Facebook Page ID** and **Instagram Account ID**: leave empty. The check fills them in.
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

## 8. Live test

Use a personal account that has **no role on the app**, so you see what customers see. For each
step, watch the Railway worker logs. The web service only logs warnings and errors.

1. Send the Page a Messenger DM. The agent should reply.
2. Comment on a Facebook post. You should get a public reply on the comment, plus the answer in DM.
3. Send the Instagram account a DM. The agent should reply.
4. Comment on an Instagram post. You should get a public reply, plus the answer in DM.

**Works from your own account but not from a customer's:**
1. Check the app is **Live**: look at the app dashboard's top bar. In Development mode only accounts
   with a role on the app get replies. Publish it (step 3).
2. If it's Live and still only your account gets replies, that event needs Advanced Access (step 9).

**Doesn't arrive even from your own account:** recheck steps 5 and 7.

**Testing comments from the admin account:** switch to your personal profile first. On your own Page,
Facebook often posts as the Page, and the agent ignores the Page's own comments so it never replies to
itself.

## 9. App Review (Advanced Access)

Needed for every event that only arrives from accounts with a role on the app.

1. **Business Verification**: Meta Business Suite → Settings → **Security Center** →
   **Start verification**. Meta asks for the business's legal documents.
2. **App Review**: in the app, request **Advanced Access** for the permissions behind the events that
   don't arrive yet (each permission's **Actions → Add to App Review**):
   - Messenger: `pages_messaging`
   - Facebook comments: `pages_manage_metadata`, `pages_read_engagement`, `pages_manage_engagement`
   - Instagram: `instagram_manage_messages`, `instagram_manage_comments`

   Then **Review → App Review → Submit for Review**, with:
   - a screencast of the agent replying to a DM and a comment;
   - reviewer instructions.

## 10. After it works

Set `META_REQUIRE_WEBHOOK_SIGNATURE=True` on Railway. From then on, a webhook without a valid
signature from the app secret is rejected, even for a store with no secret saved.
