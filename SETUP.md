# Lucid — full setup guide

This guide walks you through setting up Lucid from scratch. If you just want the
short version, see the Quick Start in [README.md](README.md). Nothing here is
hard — take it one step at a time.

Lucid runs on **your own computer** (Windows, macOS, or Linux). Your recordings
and notes stay with you.

---

## What you'll need before you start

- **Python 3.11 or newer** — [python.org/downloads](https://www.python.org/downloads/).
- A **Plaud** recorder, with the Plaud phone app installed.
- An **Anthropic API key** (we'll get this together below).
- A few minutes.

You do **not** need to be technical. You'll type one command to start, and the
rest happens in your web browser.

---

## Step 1 — Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download
   the latest version (3.11 or newer).
2. Run the installer.
3. **Important:** on the first screen, tick **"Add Python to PATH"** before
   clicking Install. (On macOS the installer handles this for you.)

To check it worked, open a terminal and run:

```
python --version
```

You should see something like `Python 3.12.x`. If your system says
`python: command not found`, try `python3 --version` instead, and use
`python3` everywhere below.

---

## Step 2 — Download and unzip Lucid

Unzip the Lucid download somewhere easy to find, like your Desktop. You'll end
up with a folder called `lucid` (or similar).

---

## Step 3 — Start Lucid

Open a terminal **in the Lucid folder** and run:

```
python start.py
```

- **The first time**, Lucid sets up a private, self-contained workspace and
  installs everything it needs. It downloads a few hundred MB, so give it **a
  few minutes** — this only happens once. (Choosing **cloud** transcription in
  the wizard keeps the install lighter and skips the local model download.)
- After that, it starts up quickly and **opens your web browser** to the setup
  wizard.

Leave that terminal window open while you use Lucid — it's the engine running in
the background. (You can close it anytime to stop Lucid, and run `python
start.py` again to start it back up.)

---

## Step 4 — Get your Anthropic API key

This is the key that powers Lucid's smart analysis (the summaries, people,
ideas, and action items).

1. Go to [console.anthropic.com](https://console.anthropic.com) and sign in (or
   create an account).
2. Add a payment method under **Billing** (analysis costs a few cents per
   recording — see "How much does it cost?" below).
3. Open **API Keys** → **Create Key**.
4. Copy the key. It looks like `sk-ant-...`.

Paste it into the setup wizard when asked. You can change it later in Lucid's
**Settings**.

---

## Step 5 — Choose how to transcribe (speech → text)

Before Lucid can analyze a recording, it turns the speech into text. You pick
how, in the wizard:

### Local (free, fully private)

Transcription runs **on your own computer**. Your audio never leaves your
machine. This is the most private option and costs nothing. It's a bit slower,
and the first run downloads a small model file.

If you choose Local, you can also pick a **model size**. Bigger models are more
accurate but slower and use more memory:

| Model      | Accuracy   | Speed      | Good for                                  |
|------------|------------|------------|-------------------------------------------|
| `tiny`     | Basic      | Fastest    | Quick notes, older/slower computers       |
| `base`     | Good       | Fast       | A solid default for most people           |
| `small`    | Better     | Moderate   | Noticeably cleaner transcripts            |
| `medium`   | Great      | Slower     | Accent-heavy or quiet recordings          |
| `large-v3` | Best       | Slowest    | Maximum accuracy, ideally with a good GPU |

If you're not sure, **`base`** is a great starting point. You can change it
later.

### Cloud (faster, very accurate)

Transcription is done by an online service — **OpenAI** or **Deepgram**. This is
faster and handles tricky audio well, but it means your **audio is sent to that
service**, and you need an API key for it:

- **OpenAI** — get a key at
  [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
- **Deepgram** — get a key at
  [console.deepgram.com](https://console.deepgram.com). Particularly good at
  telling speakers apart.

Paste whichever key you chose into the wizard.

> **Privacy note:** with **Local**, your audio stays on your computer. With
> **Cloud**, your audio goes to OpenAI or Deepgram for transcription. In both
> cases the transcript **text** is sent to Anthropic for analysis.

---

## Step 6 — Turn on Private Cloud Sync (on your Plaud)

This is the key step that makes everything automatic. Lucid watches your Plaud
cloud and pulls in each new recording on its own — but only if your Plaud is
uploading them there.

In the **Plaud phone app**:

1. Open **Settings**.
2. Find **Private Cloud Sync** and turn it **on**.
3. (Optional) Turn on "Sync on Wi-Fi only" if you'd rather not use mobile data.

From now on, every recording uploads to your Plaud cloud automatically after you
capture it. That's the signal Lucid pulls from.

---

## Step 7 — Connect your Plaud account in the wizard

Back in the Lucid setup wizard, enter your **Plaud email and password**, and
choose your **region** (US or EU). Lucid signs in once to get a long-lived
access token; **your password itself is not stored**.

> **Sign in to Plaud with Google?** Lucid needs an email-and-password login. Go
> to [web.plaud.ai](https://web.plaud.ai), choose **Forgot Password**, set a
> password, and use that here.

---

## Step 8 — Set your app password

Finally, choose a password for Lucid itself. This protects your notes so only
you can open them — especially important because of the next step.

---

## Step 9 — Your private web link

When setup finishes, Lucid automatically creates a **private Cloudflare web
link** — a secure web address that points back to the copy of Lucid running on
your computer. This is what lets you open your notes **from your phone, from
anywhere**, not just at home.

- The link is encrypted end to end.
- Your **app password** is what keeps it private — only someone with the
  password can get in.
- It works without you having to set up port forwarding, firewalls, or anything
  on your router.

You'll see the link in Lucid. Bookmark it on your phone, or add it to your home
screen, and you're done.

---

## You're set up — here's what happens now

1. **Record** with your Plaud, like you normally would.
2. The recording **syncs to your Plaud cloud** (Private Cloud Sync).
3. Lucid **pulls it in automatically**, transcribes it, and runs the analysis.
4. A clean, sorted **note appears in Lucid** — open it on your phone or
   computer to read the summary, key points, people, ideas, action items, and
   the full searchable transcript.

You never have to touch Lucid again. Just record.

---

## Changing settings later

Open Lucid in your browser and go to **Settings**. You can update your Anthropic
key, transcription choice and model, Plaud account, and app password anytime.

To **redo the whole setup from scratch**, delete the file `data/config.json`
inside the Lucid folder and run `python start.py` again — the wizard reappears.

---

## FAQ

**How much does it cost?**
Lucid itself is free and open-source. Costs come from the services you use:
**Anthropic** charges a few cents per recording for the analysis. **Local**
transcription is free. **Cloud** transcription (OpenAI or Deepgram) has its own
small per-minute charge. There is no subscription to Lucid.

**Does my audio leave my computer?**
Only where you decide. With **Local** transcription, your audio never leaves
your machine. With **Cloud** transcription, your audio goes to OpenAI or
Deepgram. In all cases, the transcript text is sent to Anthropic for the
analysis. Nothing else is shared, and there's no central Lucid server.

**Do I have to keep my computer on?**
Yes — Lucid runs on your computer, so it needs to be on (and the `start.py`
terminal open) to pull in and process recordings. If your computer is off when
you record, Lucid will catch up the next time it's running.

**Can I get to old recordings I made before installing Lucid?**
By default Lucid only processes **new** recordings going forward. There's an
advanced option to process your existing Plaud history too (see `.env.example`),
but be aware it can process a lot of recordings at once and add up in cost.

**Is the Plaud connection official?**
Lucid connects to your Plaud cloud using your own account. It relies on Plaud's
cloud sync working as it does today. If Plaud changes how their service works,
this part may need an update.

**It says my Plaud login failed.**
Try the other region (US vs EU). If you normally sign in with Google, set a
password first at [web.plaud.ai](https://web.plaud.ai) via "Forgot Password,"
then use that.

**Nothing imports after I record.**
Check that **Private Cloud Sync** is on in the Plaud app and that the recording
finished uploading. Give it a few minutes — Lucid checks on a schedule. The
`start.py` terminal window shows what Lucid is doing.

**Can I still get audio in another way?**
Yes. You can drop audio files into the `data/inbox` folder, or upload them in
the web app, and Lucid will process them like any other recording.

**Where is everything stored?**
In the `data` folder inside Lucid: your settings (`data/config.json`), your
audio, and your notes. Back up that folder to back up everything.
