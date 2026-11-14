// Discord interactions endpoint (slash commands) on Cloudflare Workers (free tier).
// Stores one subscription per server in KV. The daily post itself is sent by GitHub Actions
// (src/skibot/discord_bot.py), which reads the subscriber list from /subscribers.
const API = "https://discord.com/api/v10";
const MANAGE_GUILD = 1n << 5n, ADMINISTRATOR = 1n << 3n;
const enc = new TextEncoder();
const hex = (s) => Uint8Array.from(s.match(/.{1,2}/g).map((b) => parseInt(b, 16)));

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const reply = (content) => json({ type: 4, data: { content, flags: 64, allowed_mentions: { parse: [] } } });

async function verifySignature(request, body, publicKey) {
  const sig = request.headers.get("X-Signature-Ed25519");
  const ts = request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !publicKey) return false;
  try {
    const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hex(sig), enc.encode(ts + body));
  } catch {
    return false;
  }
}

function authorized(request, env) {
  const h = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.BROADCAST_SECRET || ""}`;
  if (!env.BROADCAST_SECRET || h.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < h.length; i++) diff |= h.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

const isManager = (i) => {
  const p = BigInt(i.member?.permissions ?? "0");
  return (p & (MANAGE_GUILD | ADMINISTRATOR)) !== 0n;
};

async function listSubscribers(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.SUBS.list({ prefix: "sub:", cursor });
    for (const k of page.keys) {
      const v = await env.SUBS.get(k.name, "json");
      if (v) out.push({ guild_id: k.name.slice(4), ...v });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

async function handleCommand(i, env) {
  if (i.data?.name !== "ski") return reply("Unknown command.");
  if (!i.guild_id) return reply("Please use this command inside a server.");
  const sub = i.data.options?.[0];
  const opt = (name) => sub?.options?.find((o) => o.name === name)?.value;
  const key = `sub:${i.guild_id}`;
  const site = env.SITE_URL ? `\nMore info: ${env.SITE_URL}` : "";

  if (!isManager(i)) return reply("You need the **Manage Server** permission to change this setting.");

  if (sub?.name === "setup") {
    const channelId = opt("channel");
    const mention = opt("mention_everyone") ?? (env.DEFAULT_MENTION_EVERYONE === "true");
    const test = await fetch(`${API}/channels/${channelId}/messages`, {
      method: "POST",
      headers: { Authorization: `Bot ${env.DISCORD_BOT_TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({
        content: "✅ Daily ski & snow news will be posted in this channel.",
        allowed_mentions: { parse: [] },
      }),
    });
    if (!test.ok) {
      return reply(
        `I can't post in <#${channelId}>. Please give me **View Channel, Send Messages, Embed Links, Attach Files**` +
          `${mention ? " and **Mention Everyone**" : ""} in that channel, then run the command again.`
      );
    }
    // Only IDs are stored: server ID, channel ID, mention preference, timestamp. No user data.
    await env.SUBS.put(key, JSON.stringify({ channel_id: channelId, mention_everyone: !!mention, added_at: new Date().toISOString() }));
    return reply(`Done. Daily posts go to <#${channelId}>${mention ? " with an @everyone ping" : ""}.${site}`);
  }

  if (sub?.name === "status") {
    const v = await env.SUBS.get(key, "json");
    return reply(
      v ? `Active: posts go to <#${v.channel_id}> (@everyone ping: ${v.mention_everyone ? "on" : "off"}).${site}`
        : `Not set up yet. Run \`/ski setup\`.${site}`
    );
  }

  if (sub?.name === "stop") {
    await env.SUBS.delete(key);
    return reply("Stopped. No more posts will be sent to this server. Your data has been deleted.");
  }
  return reply("Unknown subcommand.");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/interactions" && request.method === "POST") {
      const body = await request.text();
      if (!(await verifySignature(request, body, env.DISCORD_PUBLIC_KEY))) return new Response("Bad signature", { status: 401 });
      const i = JSON.parse(body);
      if (i.type === 1) return json({ type: 1 }); // PING
      if (i.type === 2) return handleCommand(i, env);
      return reply("Unsupported interaction.");
    }

    if (url.pathname === "/subscribers" && request.method === "GET") {
      if (!authorized(request, env)) return new Response("Unauthorized", { status: 401 });
      return json({ subscribers: await listSubscribers(env) });
    }

    if (url.pathname === "/unsubscribe" && request.method === "POST") {
      if (!authorized(request, env)) return new Response("Unauthorized", { status: 401 });
      const { guild_id } = await request.json();
      if (!/^\d+$/.test(String(guild_id))) return new Response("Bad request", { status: 400 });
      await env.SUBS.delete(`sub:${guild_id}`);
      return json({ ok: true });
    }

    return new Response("Ski news bot", { status: 200 });
  },
};
