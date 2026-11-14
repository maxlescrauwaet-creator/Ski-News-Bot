// Offline test for worker.js: node bot/test.mjs  (Node 20+)
import worker from "./worker.js";
import assert from "node:assert";
import { webcrypto as crypto } from "node:crypto";

const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const pub = Buffer.from(await crypto.subtle.exportKey("raw", kp.publicKey)).toString("hex");
const kv = new Map();
const env = {
  DISCORD_PUBLIC_KEY: pub, DISCORD_BOT_TOKEN: "t", BROADCAST_SECRET: "s3cret", DEFAULT_MENTION_EVERYONE: "false",
  SUBS: {
    put: async (k, v) => void kv.set(k, v),
    get: async (k) => (kv.has(k) ? JSON.parse(kv.get(k)) : null),
    delete: async (k) => void kv.delete(k),
    list: async ({ prefix }) => ({ keys: [...kv.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true }),
  },
};
let discordOk = true;
globalThis.fetch = async () => new Response("{}", { status: discordOk ? 200 : 403 });

async function signed(body, bad = false) {
  const ts = String(Date.now());
  const sig = Buffer.from(await crypto.subtle.sign("Ed25519", kp.privateKey, new TextEncoder().encode(ts + (bad ? "x" : body)))).toString("hex");
  return new Request("https://w/interactions", { method: "POST", body, headers: { "X-Signature-Ed25519": sig, "X-Signature-Timestamp": ts } });
}
const cmd = (sub, options, perms = "32") => JSON.stringify({ type: 2, guild_id: "111", member: { permissions: perms }, data: { name: "ski", options: [{ name: sub, options }] } });
const text = async (r) => (await r.json()).data?.content;

assert.equal((await (await worker.fetch(await signed('{"type":1}'), env)).json()).type, 1, "ping");
assert.equal((await worker.fetch(await signed('{"type":1}', true), env)).status, 401, "bad signature rejected");
assert.match(await text(await worker.fetch(await signed(cmd("setup", [{ name: "channel", value: "222" }], "0")), env)), /Manage Server/, "non-admin blocked");
discordOk = false;
assert.match(await text(await worker.fetch(await signed(cmd("setup", [{ name: "channel", value: "222" }])), env)), /can't post/, "missing perms reported");
assert.equal(kv.size, 0);
discordOk = true;
assert.match(await text(await worker.fetch(await signed(cmd("setup", [{ name: "channel", value: "222" }, { name: "mention_everyone", value: true }])), env)), /@everyone ping/);
assert.equal(JSON.parse(kv.get("sub:111")).mention_everyone, true);
assert.match(await text(await worker.fetch(await signed(cmd("status", [])), env)), /Active/);
assert.equal((await worker.fetch(new Request("https://w/subscribers"), env)).status, 401, "subscribers needs secret");
const subs = await (await worker.fetch(new Request("https://w/subscribers", { headers: { Authorization: "Bearer s3cret" } }), env)).json();
assert.deepEqual(subs.subscribers.map((s) => [s.guild_id, s.channel_id]), [["111", "222"]]);
await worker.fetch(new Request("https://w/unsubscribe", { method: "POST", body: '{"guild_id":"111"}', headers: { Authorization: "Bearer s3cret" } }), env);
assert.equal(kv.size, 0, "unsubscribe");
await worker.fetch(await signed(cmd("setup", [{ name: "channel", value: "222" }])), env);
assert.match(await text(await worker.fetch(await signed(cmd("stop", [])), env)), /deleted/);
assert.equal(kv.size, 0, "stop deletes data");
console.log("worker tests passed");
