// End-to-end check of Rewind through the real interface, in headless Chrome.
//
//   cd tools/e2e && npm install
//   python3 -m hindsightd --dev --data /tmp/hs-test   (in another terminal)
//   node rewind.test.mjs [baseUrl] [screenshotDir]
//
// It deletes a note, rewinds Notes, checks the note is back, undoes the
// rewind, draws in Sketch, and opens the phone-wide timeline. Screenshots
// of each step are saved so you can see what happened.
import fs from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer-core";

const BASE = process.argv[2] || "http://127.0.0.1:7070";
const OUT = process.argv[3] || "screenshots";
const CHROME = process.env.CHROME_PATH ||
  ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find((p) => fs.existsSync(p));

fs.mkdirSync(OUT, { recursive: true });
let step = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function check(cond, message) {
  if (!cond) throw new Error(`Check failed: ${message}`);
  console.log(`  ok  ${message}`);
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--font-render-hinting=none"],
});
const page = await browser.newPage();
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

async function shot(name) {
  step += 1;
  const file = path.join(OUT, `${String(step).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: file });
  console.log(`  shot ${file}`);
}
async function liveFrame() {
  const handle = await page.$("#live");
  return handle.contentFrame();
}
async function pastFrame() {
  const handle = await page.$("#past");
  return handle.contentFrame();
}
async function waitForText(frame, text, timeout = 8000) {
  const sel = frame === "past" ? "#past" : "#live";
  await page.waitForFunction((s, t) => {
    const doc = document.querySelector(s).contentDocument;
    return !!(doc && doc.body && doc.body.innerText.includes(t));
  }, { timeout }, sel, text);
}
async function waitInLive(selector, appPath, timeout = 8000) {
  await page.waitForFunction((sel, path) => {
    const f = document.querySelector("#live");
    const doc = f.contentDocument;
    return f.getAttribute("src") && f.getAttribute("src").includes(path) &&
      doc && doc.readyState === "complete" && !!doc.querySelector(sel);
  }, { timeout }, selector, appPath);
  return liveFrame();
}

try {
  console.log("home screen");
  // (not "networkidle": the shell keeps a live event stream open)
  await page.goto(BASE, { waitUntil: "load" });
  await page.waitForFunction(() => document.querySelectorAll("#grid .icon").length >= 3);
  await sleep(700);
  await shot("home");

  console.log("notes: delete the welcome note");
  await page.click('#grid .icon[aria-label="Notes"], #grid .icon');
  let live = await liveFrame();
  await waitForText("live", "Try Rewind");
  await shot("notes");
  await live.click(".list button");
  await live.waitForSelector("textarea");
  await shot("note-open");
  await live.click('button[aria-label="Delete note"]');
  await waitForText("live", "No notes");
  check(true, "note deleted");
  await sleep(300);
  await shot("note-deleted");

  console.log("rewind notes");
  await page.click("#rewindBtn");
  await page.waitForSelector(".screen.rewinding");
  await sleep(500);
  const nowText = await page.$eval("#rwRel", (n) => n.textContent);
  check(nowText === "Now", `dial starts at "Now" (got "${nowText}")`);
  await shot("rewind-now");

  await page.focus("#dial");
  await page.keyboard.press("ArrowLeft");
  await page.waitForFunction(() => document.querySelector(".screen").classList.contains("viewing-past"), { timeout: 8000 });
  const what = await page.$eval("#rwWhat", (n) => n.textContent);
  check(what.includes("Before deleting"), `first point back is the delete (got "${what}")`);
  await waitForText("past", "Try Rewind");
  check(true, "preview shows the deleted note");
  await sleep(400);
  await shot("rewind-past");

  await page.click("#rwRestore");
  await page.waitForFunction(() => {
    const t = document.querySelector("#toast");
    return !t.hidden && /back to|already/.test(document.querySelector("#toastMsg").textContent);
  }, { timeout: 8000 });
  const toastText = await page.$eval("#toastMsg", (n) => n.textContent);
  check(toastText.startsWith("Notes is back to"), `toast confirms restore (got "${toastText}")`);
  live = await liveFrame();
  await waitForText("live", "Try Rewind");
  check(true, "note is back in the live app");
  await sleep(500);
  await shot("restored");

  console.log("undo the rewind");
  await page.click("#toastAction");
  await page.waitForFunction(() => document.querySelector("#toastMsg").textContent.startsWith("Undone"));
  live = await liveFrame();
  await waitForText("live", "No notes");
  check(true, "undo removes the note again");

  console.log("rewind again so the demo note stays");
  await page.click("#rewindBtn");
  await page.waitForSelector(".screen.rewinding");
  await sleep(400);
  // walk back until the delete point is selected
  for (let i = 0; i < 6; i++) {
    const w = await page.$eval("#rwWhat", (n) => n.textContent);
    if (w.includes("Before deleting")) break;
    await page.focus("#dial");
    await page.keyboard.press("ArrowLeft");
    await sleep(350);
  }
  await page.click("#rwRestore");
  live = await liveFrame();
  await waitForText("live", "Try Rewind");
  check(true, "restored again");

  console.log("sketch");
  await page.click("#homeBtn");
  await page.waitForSelector("#home:not([hidden])");
  const icons = await page.$$("#grid .icon");
  await icons[1].click();
  live = await waitInLive("canvas", "/apps/sketch/");
  await sleep(500);
  const box = await (await page.$("#live")).boundingBox();
  const draw = async (pts) => {
    await page.mouse.move(box.x + pts[0][0], box.y + pts[0][1]);
    await page.mouse.down();
    for (const [x, y] of pts.slice(1)) await page.mouse.move(box.x + x, box.y + y, { steps: 6 });
    await page.mouse.up();
    await sleep(250);
  };
  await draw([[60, 200], [140, 140], [220, 260], [320, 180]]);
  await draw([[80, 420], [200, 360], [300, 470]]);
  await sleep(500);
  await shot("sketch");
  const saved = await live.evaluate(() => Hindsight.storage.get("drawing"));
  check(saved && saved.strokes.length === 2, "two strokes saved");

  console.log("phone-wide rewind");
  await page.click("#homeBtn");
  await page.waitForSelector("#home:not([hidden])");
  await page.click("#rewindBtn");
  await page.waitForSelector(".screen.rewinding");
  await sleep(500);
  await page.focus("#dial");
  await page.keyboard.press("ArrowLeft");
  await sleep(600);
  await shot("phone-rewind");
  await page.click("#rwNow");
  await sleep(200);
  await page.click("#rwNow");
  await sleep(500);

  console.log("settings");
  await (await page.$$("#grid .icon"))[2].click();
  await waitForText("live", "About this phone");
  await sleep(500);
  await shot("settings");

  check(errors.length === 0, `no page errors${errors.length ? ": " + errors.join("; ") : ""}`);
  console.log("\nAll Rewind checks passed.");
} catch (err) {
  await shot("failure").catch(() => {});
  console.error(`\n${err.message}`);
  const state = await page.evaluate(() => ({
    screen: document.querySelector(".screen").className,
    live: document.querySelector("#live").getAttribute("src"),
    appHidden: document.querySelector("#app").hidden,
    homeHidden: document.querySelector("#home").hidden,
    toast: document.querySelector("#toast").hidden ? null : document.querySelector("#toastMsg").textContent,
  })).catch(() => null);
  console.error("Shell state:", JSON.stringify(state));
  if (errors.length) console.error("Page errors:", errors);
  process.exitCode = 1;
} finally {
  await browser.close();
}
