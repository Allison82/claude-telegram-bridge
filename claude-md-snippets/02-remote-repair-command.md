## ⚡ Remote repair command (Telegram only)

If a message received from the Telegram channel is **exactly** the word
"repair" (after trimming whitespace — not a sentence that merely contains
that word), immediately — without asking for confirmation first — run these
with the Bash tool, in order:

1. `claude plugin update telegram@claude-plugins-official --yes`
2. `launchctl kickstart -k gui/$(id -u)/com.YOUR_USERNAME.claude-telegram-channel`

Notes:

- Step 2 restarts the very process you're running in. You will be cut off
  when it runs — that's expected, don't try to run anything after it or
  report a result afterward.
- If you can send a reply via the reply tool **before** step 2 ("got it,
  starting repair"), do so, so the user knows the command was received.
- Rate limit: if this repair sequence already ran in the last 5 minutes,
  just reply "repaired within the last 5 minutes, let's wait and see" and
  skip re-running it — avoid a restart loop.

Limitation to be upfront about: this only fixes infrastructure-layer
problems (dead process, stale plugin version). It cannot fix a logic/prompt
bug like the one described in `01-telegram-channel.md` — those require a
human to notice the odd behavior and edit this file.
