[README.md](https://github.com/user-attachments/files/34853272/README.md)
# Discord bot for other servers (`/ski setup`)

Anyone with **Manage Server** can add the bot, run `/ski setup channel:#news`, and the daily post is delivered to that channel.
Everything stays free:

```
Discord  --slash commands-->  Cloudflare Worker (bot/worker.js) + KV   (stores server ID, channel ID, mention flag)
GitHub Actions (daily post)  --GET /subscribers-->  Worker  ->  POST /channels/{id}/messages  (bot token)
```

No always-on bot process is needed: the Worker only answers commands, and the existing GitHub Actions job does the sending.

## One-time setup

1. **Discord application:** discord.com/developers/applications -> New Application. Note the *Application ID* and *Public Key*
   (General Information). Bot tab -> Reset Token -> copy the **bot token**. No privileged intents are needed.
2. **Cloudflare:** free account, then in `bot/`:
   ```
   npx wrangler login
   npx wrangler kv namespace create SUBS      # paste the id into wrangler.toml
   # edit wrangler.toml: DISCORD_PUBLIC_KEY, SITE_URL
   npx wrangler secret put DISCORD_BOT_TOKEN
   npx wrangler secret put BROADCAST_SECRET   # any long random string
   npx wrangler deploy                        # prints https://ski-news-bot.<you>.workers.dev
   ```
3. **Interactions Endpoint URL** (Discord portal -> General Information): `https://ski-news-bot.<you>.workers.dev/interactions`.
   Discord sends a signed PING; saving succeeds only if the Worker is deployed with the right public key.
4. **Register the commands:** `DISCORD_APP_ID=… DISCORD_BOT_TOKEN=… python scripts/register_commands.py`
5. **GitHub Secrets:** `DISCORD_BOT_TOKEN`, `BROADCAST_SECRET` (same value as in the Worker).
   In `config/settings.json` set `publishers.discord_bot.enabled` to `true` and `worker_url` to the Worker URL.
6. **Invite link** (for your website):
   `https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot%20applications.commands&permissions=183296`
   (183296 = View Channel + Send Messages + Embed Links + Attach Files + Mention Everyone).

Test the Worker offline any time with `node bot/test.mjs` (signature check, permission check, subscribe/stop, auth).

## @everyone

* Your own server through the webhook: `publishers.discord.mention_everyone` (default `true`).
* Other servers: each admin chooses with `/ski setup … mention_everyone:true|false`. If they do not choose, the default is
  `DEFAULT_MENTION_EVERYONE` in `wrangler.toml` (shipped as `"false"`). Set it to `"true"` if you want every server pinged
  by default, but note that a daily ping from a bot many admins did not expect is the quickest way to get the bot removed or
  reported. The bot also needs the *Mention Everyone* permission in that channel, otherwise Discord silently shows plain text.
* Only `@everyone` can ever ping; role/user mentions in article text are blocked by `allowed_mentions`.

## Commands (for your website)

| Command | Who | What it does |
|---|---|---|
| `/ski setup channel:#x [mention_everyone]` | Manage Server | Checks the bot can post there, then subscribes the server |
| `/ski status` | Manage Server | Shows channel and ping setting |
| `/ski stop` | Manage Server | Unsubscribes and deletes the server's data |

## Things your website must cover (you are in the EU)

* **Privacy policy:** stored data = server ID, channel ID, @everyone preference, timestamp (no user IDs, no message content).
  Deleted on `/ski stop` or when the channel disappears. Name yourself as the controller and give a contact address.
  Linking the policy and terms in the Discord developer portal is expected.
* **Terms / content notice:** posts contain short snippets from publishers with source links and CC-licensed photos with credit.
  Say so, and say you are not affiliated with the publishers.
* **Honest limits:** posts are automated and not human-fact-checked; one post per day; the time is set by the bot owner, not per server.
* **Growth limits:** unverified Discord bots are capped at 100 servers; beyond that you need Discord's verification. A daily
  broadcast takes ~0.4 s per server, so a few hundred servers fit in the GitHub Actions run; thousands need a different host.
