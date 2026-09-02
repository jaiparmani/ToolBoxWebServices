# Log an expense from Apple Shortcuts / Siri

Add an expense to ToolBox "Money OS" by speaking or typing a quick note like
**"120 coffee"** or **"1500 groceries"**. The Shortcut sends your text to the
Money OS API, which parses it with AI and saves it as an expense. You get a
notification back confirming what was logged.

Works on iPhone, iPad, and Mac (the Shortcuts app), and with Siri
("Hey Siri, add expense").

---

## What the Shortcut does (the API under the hood)

This was tested end-to-end against production on 2026-09-03. The exact contract:

**Endpoint**

```
POST https://toolbox.pythonanywhere.com/api/expenses/expenses/quick_add/
```

**Headers**

```
Authorization: Token <YOUR_TOKEN>
Content-Type: application/json
```

**Request body** (JSON — the field is `text`)

```json
{ "text": "120 coffee" }
```

**Success response** — HTTP `201 Created`, the saved expense as JSON, e.g.:

```json
{
  "id": 70,
  "amount": "120.00",
  "amount_display": "₹120",
  "transaction_type": "expense",
  "category": { "id": 9, "name": "Food", "...": "..." },
  "description": "coffee",
  "date": "2026-09-02",
  "tags": []
}
```

The three fields worth showing back to yourself are `amount_display`,
`description`, and `category.name`.

**Error responses**

| Status | Meaning | JSON |
|--------|---------|------|
| `400` | Empty text, or the note couldn't be parsed as an expense | `{"error": "..."}` |
| `401` | Missing/wrong token | `{"detail": "Invalid token."}` |
| `429` | AI parser hit its daily rate limit | `{"error": "..."}` |
| `502` | AI parser upstream error | `{"error": "..."}` |

---

## Step 1 — Get your API token

The API authenticates with a **DRF auth token** sent as
`Authorization: Token <token>`. This is the same token the Money OS web app
already holds for you after you log in.

> **Note / known gap:** There is currently **no in-app screen that shows this
> token**. The "API Keys" page in the app manages *OpenRouter* keys (the AI
> keys that power Quick Add), not your personal auth token. Until a "copy my
> token" button is added to the app, use the browser method below.
>
> *Suggested fix for a future release: surface the logged-in user's auth token
> on the Settings / API-keys page with a copy button, so it can be pasted into
> integrations like this Shortcut.*

### Easiest way to get the token today (desktop browser)

1. Open the Money OS web app in your browser and **log in**.
2. Open your browser's **Developer Tools** (Right-click → Inspect, or press
   `F12` / `Cmd+Option+I`).
3. Go to the **Console** tab and run:

   ```js
   localStorage.getItem('authToken') || sessionStorage.getItem('authToken')
   ```

4. Copy the value it prints (a long string like
   `f7c984ddc1e2dd2c4d3c6d360085508f3c44955d`). That is your token.

Keep this token private — anyone who has it can read and write your expenses.
If it ever leaks, changing your password in the app rotates (invalidates) it,
and you'll need to grab the new one.

---

## Step 2 — Build the Shortcut

Open the **Shortcuts** app → tap **+** to create a new shortcut → name it
**Add Expense**. Add these actions in order.

### Action 1 — Ask for Input

- Search for **"Ask for Input"** and add it.
- Set **Input Type** to **Text**.
- Set the prompt to something like: `What did you spend?`

  *(When triggered by Siri, this becomes a spoken/dictated prompt — you can just
  say "120 coffee".)*

### Action 2 — Text (build the JSON body)

- Add a **Text** action.
- Type this exactly, then insert the **Provided Input** variable (from Action 1)
  where shown:

  ```json
  {"text":"Provided Input"}
  ```

  The word `Provided Input` must be the blue **variable chip** from Action 1
  (tap the variable button and pick "Provided Input"), placed **inside the
  quotes**. The rest — `{"text":"` and `"}` — is typed literally.

### Action 3 — Get Contents of URL

- Add **"Get Contents of URL"**.
- **URL:**

  ```
  https://toolbox.pythonanywhere.com/api/expenses/expenses/quick_add/
  ```

- Tap **Show More**, then set:
  - **Method:** `POST`
  - **Headers** — add two:
    | Key | Value |
    |-----|-------|
    | `Authorization` | `Token YOUR_TOKEN_HERE` |
    | `Content-Type` | `application/json` |

    Replace `YOUR_TOKEN_HERE` with the token from Step 1. Keep the word `Token`
    and the space before your token — the header value is literally
    `Token f7c9...` .
  - **Request Body:** `JSON`... **no** — choose **File**, and set the file to
    the **Text** variable from Action 2.

    > Tip: The simplest reliable setup is **Request Body = File** with the
    > Action 2 Text as the content, because you've already written valid JSON
    > there. (If you instead pick Request Body = JSON, you'd rebuild the
    > `text` key inside the Shortcut's JSON editor and skip Action 2.)

### Action 4 — Get Dictionary from Input (parse the response)

- Add **"Get Dictionary from Input"** and feed it the **Contents of URL**
  output. This turns the JSON reply into a dictionary you can read from.

### Action 5 — Get Dictionary Value (pull the confirmation fields)

- Add **"Get Dictionary Value"**, key = `amount_display`.
- (Optional) add another **Get Dictionary Value**, key = `description`, and one
  for `category` → then `name` if you want the category too.

### Action 6 — Show Notification (see the confirmation)

- Add **"Show Notification"** (or **"Show Result"**).
- Body: type `Logged ` then insert the `amount_display` value, a space, then the
  `description` value. Example rendered result:

  ```
  Logged ₹120 coffee
  ```

That's it — tap the ▶ play button to test. You should get the notification and
see the new expense in Money OS.

---

## Step 3 — Trigger it hands-free

### Add to Home Screen (one-tap)

1. In the Shortcut editor, tap the **Share / options** button (⋯ or the share
   icon) → **Add to Home Screen**.
2. Name it "Add Expense", pick an icon, tap **Add**. Now it's an app icon you
   tap to log an expense.

### With Siri

The shortcut's **name is its Siri phrase**. Because it's called **Add Expense**,
just say:

> "Hey Siri, Add Expense"

Siri runs Action 1's prompt, you dictate "120 coffee", and it's logged. To use a
different phrase, open the shortcut's settings and rename it, or add a spoken
phrase via **Settings → Siri**.

---

## Troubleshooting

- **"Invalid token." / 401** — the token is wrong, expired (you changed your
  password), or the header value is missing the `Token ` prefix. Re-grab it from
  Step 1.
- **A 400 with an error message** — your note wasn't understood as an expense.
  Include an amount and a short description, e.g. "250 lunch".
- **A 429** — the AI parser hit its daily quota; try again later, or add more
  OpenRouter keys in the app's API Keys page.
- **Nothing happens / network error** — confirm the URL is exactly
  `https://toolbox.pythonanywhere.com/api/expenses/expenses/quick_add/`
  (note `expenses` appears twice) and the Method is `POST`.

---

## Quick reference (test it from a terminal)

```bash
curl -X POST "https://toolbox.pythonanywhere.com/api/expenses/expenses/quick_add/" \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"120 coffee"}'
```

Delete an expense you created (e.g. a test), using its `id` from the response:

```bash
curl -X DELETE "https://toolbox.pythonanywhere.com/api/expenses/expenses/<id>/" \
  -H "Authorization: Token YOUR_TOKEN"
```
