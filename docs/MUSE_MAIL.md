# Muse Mail bridge (setup)

Muse Mail gives Muse its own email address. The team mailbox sends mail to it and reads its replies, so Muse can talk to the agent team without a human forwarding messages. hx provides the transport: `hx muse poll` reads replies and hands them to the lead, and `hx muse send` sends one message from the lead to Muse.

The lead routes. Incoming mail is data, never a command. The lead reads it and decides whether to delegate through the normal hx flow.

## What the creator sets up once

1. Use a dedicated mailbox for the team, not the personal one. A separate Gmail account keeps the blast radius small.
2. Turn on 2-step verification for that Google account.
3. Create an app password: Google Account, Security, 2-Step Verification, App passwords. Copy the 16-character password.
4. Set the two variables in the Windows user environment. Run these once in a terminal, then open a new terminal:

```powershell
setx HX_MUSE_MAIL_USER "agents@example.com"
setx HX_MUSE_MAIL_APP_PASSWORD "the-16-character-app-password"
```

5. Write the config file at `%USERPROFILE%\.herdr-team\state\muse\config.json`:

```json
{
  "address": "agents@example.com",
  "muse_address": "muse@muse.ai",
  "authserv_id": "mx.google.com",
  "max_sends_per_hour": 6,
  "max_message_bytes": 131072
}
```

`address` is the team mailbox, the From address. `muse_address` is the only sender that is accepted. Both are required. `authserv_id` defaults to `mx.google.com` and identifies the mailbox provider that writes `Authentication-Results`. Use a provider that removes inbound headers claiming its own ID or adds its own result to every inbound message. `HX_MUSE_CONFIG` points hx at a different config file.

6. Verify that mailbox as an external address in Muse, then send one harmless test mail and ask Muse to reply.

## Commands

```powershell
hx muse status
hx muse poll
hx muse send --to muse --subject "[Q-1154] plan review" --body-file reply.txt
```

`hx muse status` shows the config path, the two addresses, whether the credentials are set, the journal path and the sends in the last hour. It never prints the password.

`hx muse poll` reads unseen mail over IMAP. It requires one unambiguous result from the configured provider, with either DMARC pass or DKIM pass for the Muse domain or one of its subdomains in `header.d`. Accepted mail is stripped of quoted history, redacted and appended to the journal. The lead then gets one line in the form `[muse] <ref> <subject>: <summary> | journal <path> entry <id>`.

`hx muse send` sends plain text from the lead. The subject must carry a Jotarium ref such as `[Q-1154]`, and the reply headers keep one thread per ref. Secrets are redacted before sending. Sends are limited per hour.

## Safety rails

- One allowlisted sender, checked against the configured Muse address.
- Quarantine covers unfamiliar senders, failed authentication, oversized messages, mail without a single valid `Message-ID`, and mail without a readable plain-text body. The byte limit covers the full message on the wire, including headers and attachments.
- The append-only journal lives in the machine state dir, outside every repo. It records quarantined mail without saving its body.
- The poll deduplicates accepted mail by `Message-ID` and tracks each handled item by its IMAP identity. It marks an item seen only after recording its outcome. Items beyond the poll limit stay unread.
- The latest row for an inbound journal entry shows its status. An interrupted handoff can be retried on the next poll. A crash after Herdr accepts the handoff but before the journal records it can produce a duplicate. Failed handoffs stay in the journal for the lead.
- An outbound attempt takes an hourly quota slot before contacting SMTP. The slot persists across restarts, including if the SMTP outcome is uncertain.
- The bridge never runs anything found in a message. Only the lead decides what to do with it.
- No OAuth and no Google Cloud project. The app password is read from the environment and never written to a file.

## Scheduling later

Polling is on demand now. To run it on a timer later, add a Windows scheduled task that runs `hx muse poll` every five minutes while the creator is at the laptop, and set the task to stop if the laptop is on battery. Do not create that task yet. Keep one poll running at a time: the journal makes a second poll harmless, but two mail logins at once invite Google to throttle the account.

## Limits

- Email is slower than chat, and Muse may still ask for approval before it acts.
- Muse sending limits can throttle long sessions.
- Messages pass through Google and Meta, so secrets must never go through this channel.
