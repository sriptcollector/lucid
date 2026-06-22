# Lucid

**Your own private AI notetaker for the Plaud voice recorder.**

You record on your Plaud, and Lucid quietly turns each recording into a clean,
sorted note: a short summary, the key points, the people involved, the ideas
worth keeping, and a tidy list of action items — all searchable, all in one
place you can open from your phone.

Lucid runs entirely on **your own computer**. Your recordings and notes stay
with you. The only things that ever leave your machine are the API calls you
make to your chosen AI services (Anthropic for the analysis, and a
transcription service if you pick the cloud option).

---

## What you get

- **Clean notes, automatically.** Record on your Plaud, and a finished note
  appears on its own — no exporting, no copying files, no buttons to press.
- **A summary and key points** for every recording, so you can catch up in
  seconds.
- **People, ideas, and action items** pulled out for you and kept organized.
- **Full searchable transcript**, synced to the audio so you can jump to any
  moment.
- **Open it anywhere.** Lucid sets up a private web link so you can read your
  notes from your phone, protected by your own password.

---

## Quick start (about 5 minutes)

1. **Install Python 3.11 or newer.**
   Download it from [python.org/downloads](https://www.python.org/downloads/).
   On the installer, tick **"Add Python to PATH."**

2. **Download Lucid and unzip it** somewhere easy to find, like your Desktop.

3. **Start Lucid.** Open a terminal in the Lucid folder and run:

   ```
   python start.py
   ```

   The first time, this builds a private workspace and installs everything it
   needs. The first run downloads a few hundred MB, so give it a few minutes —
   it only happens once, and after that startup is instant. (Your very first
   recording also downloads a transcription model once, unless you choose cloud
   transcription in the wizard.)

4. **Follow the setup wizard.** Your browser opens to a clean setup page. You
   will enter a few things (details below), and Lucid takes care of the rest.

That's the whole installation. From then on you just record on your Plaud and
your notes show up.

---

## What the setup wizard asks for

The wizard walks you through four things, in plain language:

1. **Your Anthropic API key.** This is what powers the smart analysis. Get one
   at [console.anthropic.com](https://console.anthropic.com) → **API Keys** →
   **Create Key**. It looks like `sk-ant-...`. (Anthropic charges a few cents
   per recording.)

2. **How to transcribe your audio (turn speech into text).** Two choices:
   - **Local** — free, and runs entirely on your computer. Nothing leaves your
     machine. A little slower, especially on older computers.
   - **Cloud** — faster and very accurate, but sends your audio to a
     transcription service and needs an OpenAI or Deepgram key.

3. **Your Plaud account.** Enter your Plaud email and password. Lucid checks
   your Plaud cloud on a schedule and pulls in each new recording for you. For
   this to work you must turn on **Private Cloud Sync** on your Plaud device
   (see [SETUP.md](SETUP.md)). Your password is used once to sign in and is not
   stored.

4. **An app password.** This is the password that protects Lucid so only you
   can open your notes.

When you finish, Lucid automatically creates a **private Cloudflare web link**
so you can open your notes from your phone, anywhere — your app password keeps
it private.

For a fuller walkthrough (including Private Cloud Sync and transcription
trade-offs), see **[SETUP.md](SETUP.md)**.

---

## How it works, day to day

```
You record on    ─►  Plaud cloud   ─►  Lucid pulls it   ─►  transcribe  ─►
 your Plaud          (auto sync)        automatically         (text)

  ─►  translate  ─►  Claude analysis  ─►  a clean note on your phone
       (optional)     (summary, people,
                       ideas, actions)
```

1. You record with your Plaud, like normal.
2. The recording syncs to your Plaud cloud.
3. Lucid notices it, pulls it in, and runs it through transcription and
   analysis.
4. A finished, sorted note appears in Lucid — open it on your phone or computer.

---

## Privacy

Your recordings and notes are stored **only on your computer**, in the `data`
folder inside Lucid. The audio is sent only to the services you choose:

- **Anthropic** receives the transcript text for analysis.
- A **transcription service** receives your audio **only if** you pick the
  cloud option. If you pick **Local**, your audio never leaves your machine.

The private web link is encrypted and protected by your app password. There is
no central Lucid server, no account to sign up for, and nobody else can see
your notes.

Please record responsibly — within the law and with the consent of the people
you record.

---

## Requirements

- **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/)).
- A **Plaud** recorder with **Private Cloud Sync** turned on.
- An **Anthropic API key** (for analysis).
- For **cloud** transcription only: an **OpenAI** or **Deepgram** key.
- Works on **Windows, macOS, and Linux**.

---

## Troubleshooting

**"python is not recognized" / wrong version.**
Lucid needs Python 3.11+. Reinstall from
[python.org](https://www.python.org/downloads/) and tick **"Add Python to
PATH."** Check your version with `python --version`. On some systems Python is
called `python3` — try `python3 start.py`.

**I want to change a setting later.**
Open Lucid in your browser and go to **Settings**. You can update your API
keys, transcription choice, password, and Plaud account there.

**I want to redo the whole setup.**
Your settings live in the file `data/config.json` inside the Lucid folder.
Delete that file (or just clear it) and run `python start.py` again — the setup
wizard will reappear.

**Where is my data?**
Everything — your config, audio, and notes — lives in the `data` folder inside
Lucid. Back up that folder and you've backed up everything.

**New recordings aren't showing up.**
Make sure **Private Cloud Sync** is enabled on your Plaud and that the
recording actually finished syncing. See [SETUP.md](SETUP.md) for details.

---

## For developers

Lucid is a **FastAPI** backend serving a **vanilla-JavaScript single-page app**
(no build step). All settings funnel through a single `settings` object in
`server/config.py`, backed by `data/config.json` and optional environment
overrides (see `.env.example`).

**Run it directly** (skipping `start.py`):

```
python -m server.main
```

**The pipeline.** Each recording flows through three stages, persisted as it
goes:

```
transcribe  ─►  translate  ─►  analyze
 (local Whisper /   (optional,    (Claude returns a structured
  OpenAI / Deepgram)  to English   note: summary, people, ideas,
                      or another    action items)
                      language)
```

**Project layout** (high level):

```
start.py                 First-run installer + launcher (creates the venv).
server/
  main.py                FastAPI app: REST API, static UI, background workers.
  config.py              The single settings object (data/config.json + env).
  pipeline/
    transcribe.py        Local Whisper | OpenAI | Deepgram (pluggable).
    translate.py         Optional translation step.
    analyze.py           Claude analysis -> structured note.
    runner.py            Orchestrates the stages and saves each result.
  ingest/                Pulls recordings from your Plaud cloud + a drop folder.
web/                     The vanilla-JS single-page app (setup wizard + notes).
data/                    Your config, audio, and notes (created on first run).
```

**Swapping pieces.** Add a transcription backend in
`server/pipeline/transcribe.py`; change what the analysis extracts by editing
the tool schema in `server/pipeline/analyze.py`.

---

## License

MIT — open by design.
