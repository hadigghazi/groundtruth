// Drives the built app in headless Chromium and screenshots every screen, in English and
// Arabic, from kick-off to the full-time answer key. Run against a live server:
//   BASE=http://localhost:8000 OUT=shots node e2e/screens.mjs
// Page errors fail the run.

import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE ?? "http://localhost:8000";
const out = process.env.OUT ?? "shots";
const speed = Number(process.env.SPEED ?? 30);
mkdirSync(out, { recursive: true });

const errors = [];
const api = async (path, body) => {
  const r = await fetch(base + path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : undefined);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
};
const until = async (what, fn, timeoutS) => {
  const end = Date.now() + timeoutS * 1000;
  while (Date.now() < end) { if (await fn()) return true; await new Promise((r) => setTimeout(r, 2000)); }
  console.log(`gave up waiting for ${what}`);
  return false;
};

const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => { errors.push(e.message); console.log("pageerror:", e.message); });
page.on("console", (m) => { if (m.type() === "error") console.log("console error:", m.text()); });
const shot = async (name) => { await page.screenshot({ path: `${out}/${name}.png` }); console.log("shot", name); };

await page.goto(base);
await page.waitForSelector(".kickoff .club");
await shot("01-home");

const { match_id: id } = await api("/api/matches", { home: "RVM", away: "HLB", seed: 7, speed, scripted: true });
await page.goto(`${base}/#match=${id}`);
await page.waitForSelector(".howto"); // opens by itself on a first visit
await shot("02a-howto");
await page.getByRole("button", { name: "Got it" }).click();
await page.waitForSelector(".gt-bug");
await page.waitForTimeout(20000);
await shot("02-broadcast");

await until("an agent run", async () => (await api(`/api/matches/${id}/agents`)).runs.length > 0, 240);
await page.waitForTimeout(2000);
await shot("03-agents");
await page.getByRole("tab", { name: /Metrics/ }).click();
await shot("04-performance");
await page.getByRole("tab", { name: /Director/ }).click();
await shot("05-director");
await page.getByRole("tab", { name: /Stats/ }).click();
await shot("06-stats");

try {  // the personal overlays: one metric to watch, a player to follow, the player cam
  await page.getByRole("combobox", { name: "Watch" }).first().click();
  await page.getByRole("option", { name: "Possession", exact: true }).first().click();
  await page.getByRole("combobox", { name: "Follow" }).first().click();
  await page.getByRole("option").nth(4).click();
  await page.getByRole("button", { name: "Camera follows the player" }).first().click();
  await page.waitForTimeout(4000);
  await shot("06b-focus");
} catch (e) { console.log("focus step skipped:", e.message); }

await until("a stat panel on screen", async () => (await page.locator(".gt-panel").count()) > 0, 200);
await shot("07-stat-panel");
if (await page.locator(".gt-panel").count()) {
  await page.locator(".gt-panel").first().click();
  await page.waitForTimeout(600);
  await shot("08-evidence");
  await page.getByRole("button", { name: "Close" }).click();
}

await page.getByRole("button", { name: "Catch me up" }).first().click();
await page.waitForTimeout(2500);
await shot("08b-catch-up");
await page.locator(".gt-recap").getByRole("button", { name: "Close" }).click().catch(() => undefined);
await page.getByRole("tab", { name: /Ask/ }).click();
await page.locator(".ask-suggest button").first().click();
await page.waitForTimeout(3000);
await shot("08c-ask");
await page.getByRole("tab", { name: /Stats/ }).click();

await page.getByRole("button", { name: /^Compare 4 viewers/ }).click();
await page.mouse.move(10, 890); // move off the tooltip
await page.waitForTimeout(3000);
await shot("09-four-up");
await page.getByRole("tab", { name: /Tactical/ }).click();
await page.waitForTimeout(2000);
await shot("10-tactical-four-up");
await page.getByRole("button", { name: "One viewer" }).click();
await page.getByRole("tab", { name: /Broadcast/ }).click();

await until("full time", async () => (await api(`/api/matches/${id}`)).finished, 420);
await page.waitForTimeout(16000); // the broadcast delay, then the answer key
await shot("11-answer-key");
if (await page.locator(".gt-ftswitch").count()) {
  await page.locator(".gt-ftswitch button").nth(1).click();
  await page.waitForTimeout(800);
  await shot("11b-recap");
  await page.locator(".gt-ftswitch button").nth(0).click();
}
await page.getByRole("tab", { name: /Scorecard/ }).click();
await shot("12-scorecard");

await page.evaluate(() => localStorage.setItem("gt-lang", "ar"));
await page.goto(base);
await page.waitForSelector(".kickoff .club");
await shot("13-home-ar");
await page.goto(`${base}/#match=${id}`);
await page.waitForSelector(".gt-bug");
await page.waitForTimeout(4000);
await shot("14-match-ar");

const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true });
await mobile.addInitScript(() => localStorage.setItem("gt-howto-seen", "1"));
const phone = await mobile.newPage();
phone.on("pageerror", (e) => { errors.push(e.message); console.log("pageerror (phone):", e.message); });
await phone.goto(base);
await phone.waitForSelector(".kickoff .club");
await phone.screenshot({ path: `${out}/15-home-phone.png`, fullPage: true });
await phone.goto(`${base}/#match=${id}`);
await phone.waitForSelector(".gt-bug");
await phone.waitForTimeout(3000);
await phone.screenshot({ path: `${out}/16-match-phone.png` });

console.log(JSON.stringify(await api(`/api/matches/${id}/agents`), null, 1).slice(0, 1500));
await browser.close();
if (errors.length) { console.log(`${errors.length} page errors`); process.exit(1); }
