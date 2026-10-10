/* Optional browser QA. Only the local synthetic demo may be used. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const credentialsFile = path.resolve(root, process.env.ARCHIVIO_UI_DEMO_CREDENTIALS || path.join("runtime", "accesso-demo.json"));
const demo = JSON.parse(fs.readFileSync(credentialsFile, "utf8"));
const historyQuery = process.env.ARCHIVIO_UI_HISTORY_QUERY || "Conserviamo il copione";
const base = "http://127.0.0.1:58540";
assert.equal(demo.url, base, "Use only the authorized local demo.");
assert.equal(demo.solo_dimostrazione, true, "Real archives are excluded.");
const output = path.join(root, "runtime", "ui-qa");
fs.mkdirSync(output, { recursive: true });
const report = { localSyntheticDemoOnly: true, checks: [], screenshots: [], errors: [] };
let browser;

function pass(name, evidence) { report.checks.push({ name, status: "PASS", evidence }); }
async function shot(page, name) {
  await page.screenshot({ path: path.join(output, name), fullPage: true });
  report.screenshots.push(name);
}
async function layout(page, name) {
  const state = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
    inlineStyles: Array.from(document.querySelectorAll("[style]")).filter((el) => el.getAttribute("style").trim()).map((el) => ({ tag: el.tagName, class: el.className, style: el.getAttribute("style") })),
    visibleOverflow: Array.from(document.querySelectorAll("main *")).filter((el) => {
      const box = el.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && box.right > innerWidth + 1 && box.left >= 0;
    }).slice(0, 8).map((el) => ({ tag: el.tagName, class: el.className }))
  }));
  assert(state.content <= state.viewport + 1, `${name}: horizontal overflow ${JSON.stringify(state)}`);
  assert.equal(state.inlineStyles.length, 0, `${name}: inline styles violate the CSP ${JSON.stringify(state.inlineStyles)}`);
  pass(name, state);
}
async function visit(page, pathname) {
  const response = await page.goto(base + pathname, { waitUntil: "networkidle" });
  assert(response && response.status() === 200, `HTTP page ${pathname}`);
  const csp = response.headers()["content-security-policy"] || "";
  assert(csp.includes("script-src 'self'") && !csp.includes("unsafe-inline"));
}

(async () => {
  const launchOptions = { headless: true };
  if (process.env.ARCHIVIO_BROWSER_CHANNEL) launchOptions.channel = process.env.ARCHIVIO_BROWSER_CHANNEL;
  browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: "it-IT" });
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return url.origin === base ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push(String(error.message)));
  page.on("console", (message) => { if (message.type() === "error") report.errors.push(message.text()); });
  await visit(page, "/accesso/");
  await shot(page, "desktop-accesso.png");
  await layout(page, "desktop login");
  await page.getByLabel("Nome utente", { exact: true }).fill(demo.username);
  await page.getByLabel("Password", { exact: true }).fill(demo.password);
  await Promise.all([page.waitForURL(base + "/"), page.getByRole("button", { name: "Entra nell’archivio" }).click()]);
  await page.waitForLoadState("networkidle");
  assert.equal(await page.locator(".production-card").count(), 3);
  const origins = await page.locator("#origine option").evaluateAll((items) => items.map((item) => item.value));
  assert.deepEqual(origins, ["", "kaapi", "factory", "altro"]);
  const originLabels = await page.locator("#origine option").allTextContents();
  assert.deepEqual(originLabels, ["Tutte le origini", "Integrazione esterna", "Generatore video", "Caricamento diretto"]);
  assert((await page.locator(".page-description").innerText()).includes("prima della pubblicazione sui social"));
  pass("login and archive", { productions: 3, originOptions: origins });
  await shot(page, "desktop-archivio.png");
  await layout(page, "desktop archive");
  await Promise.all([page.waitForURL(/origine=kaapi/), page.locator("#origine").selectOption("kaapi")]);
  assert.equal(await page.locator(".production-card").count(), 1);
  pass("origin filter", { productions: 1 });
  await page.locator('input[name="q"]').fill(historyQuery);
  await Promise.all([page.waitForURL((url) => url.searchParams.get("q") === historyQuery), page.getByRole("button", { name: "Cerca", exact: true }).click()]);
  assert.equal(await page.locator(".production-card").count(), 1);
  pass("search in historical script", { productions: 1 });
  await page.getByRole("button", { name: "Mostra elenco" }).click();
  assert(await page.locator("[data-productions]").evaluate((el) => el.classList.contains("is-list")));
  pass("card/list toggle");
  await page.locator(".card-title a").click();
  await page.waitForLoadState("networkidle");
  const productionPath = new URL(page.url()).pathname;
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 1);
  const player = await page.locator("video").evaluate(async (video) => {
    await video.play();
    const result = { duration: video.duration, width: video.videoWidth, height: video.videoHeight, playing: !video.paused };
    video.pause();
    return result;
  });
  assert(player.duration > 0 && player.width > 0 && player.playing);
  pass("authenticated native video playback", player);
  await shot(page, "desktop-produzione.png");
  await layout(page, "desktop production");
  await page.getByRole("link", { name: /^Revisioni/ }).click();
  assert(await page.locator("#revisioni").isVisible());
  assert(!(await page.locator("#copione").isVisible()));
  assert.equal(await page.locator(".revision-row").count(), 2);
  await page.locator(".revision-actions a").click();
  await page.waitForLoadState("networkidle");
  assert(new URL(page.url()).searchParams.has("revisione"));
  assert.equal(await page.locator(".status-summary strong").innerText(), "Approvata");
  pass("revision tab and historical approved revision", { revisions: 2 });
  await visit(page, productionPath);
  await page.getByRole("link", { name: /^Note e richieste/ }).click();
  assert(await page.locator("#note").isVisible());
  assert.equal(await page.locator("#modifica-target option").count(), 4);
  await shot(page, "desktop-note-richieste.png");
  pass("notes tab and modification request form");
  await visit(page, "/calendario/");
  assert.equal(await page.locator(".event").count(), 1);
  assert.equal(await page.getByRole("button", { name: "Segna come pubblicato" }).count(), 1);
  assert((await page.locator(".notice").innerText()).includes("non pubblica automaticamente"));
  await shot(page, "desktop-calendario.png");
  await layout(page, "desktop calendar");
  pass("manual publication calendar", { events: 1, actionNotSubmitted: true });
  await visit(page, "/richieste/");
  assert.equal(await page.locator(".request-card").count(), 1);
  assert((await page.locator(".notice").innerText()).includes("non significa già in lavorazione"));
  await layout(page, "desktop requests");
  await visit(page, "/guida/");
  await layout(page, "desktop guide");
  await visit(page, "/nuovo/");
  assert.equal(await page.locator("#nuova-origine").inputValue(), "altro");
  pass("manual deposit defaults to direct upload");
  assert.equal(await page.locator('input[name="video"]').getAttribute("accept"), "video/mp4,.mp4");
  assert.equal(await page.locator('form[enctype="multipart/form-data"] input[name="csrfmiddlewaretoken"]').count(), 1);
  await shot(page, "desktop-deposito.png");
  assert.equal(await page.locator(".file-picker").count(), 2, "Current file-picker markup must be served after restart.");
  assert(await page.locator('label.file-picker-button[for="video"]').isVisible(), "The enhanced picker button must be visible.");
  const [chooser] = await Promise.all([page.waitForEvent("filechooser", { timeout: 3000 }), page.locator('label.file-picker-button[for="video"]').click()]);
  await chooser.setFiles({ name: "solo-dimostrazione.mp4", mimeType: "video/mp4", buffer: Buffer.from("synthetic selection; never submitted") });
  assert.equal(await page.locator("#video").evaluate((input) => input.files.length), 1);
  assert.equal(await page.locator("#video ~ [data-file-summary]").innerText(), "solo-dimostrazione.mp4");
  await page.locator("#video").setInputFiles([]);
  assert.equal(await page.locator("#video ~ [data-file-summary]").innerText(), "Nessun file selezionato");
  pass("Italian file picker selects and clears synthetic file", { noUploadSubmitted: true });
  await layout(page, "desktop deposit form");
  await visit(page, productionPath + "nuova-versione/");
  assert.equal(await page.locator('input[name="titolo"]').count(), 0);
  assert.equal(await page.locator('input[name="video"]').count(), 1);
  pass("new revision view", { productionFieldsOmitted: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await visit(page, "/");
  await page.getByRole("button", { name: "Mostra schede" }).click();
  const mobileFilterWidths = await page.locator(".filter-bar select").evaluateAll((items) => items.map((item) => Math.round(item.getBoundingClientRect().width)));
  assert(mobileFilterWidths.every((width) => width >= 110), "Mobile filters must expose their selected text.");
  pass("mobile filter labels remain readable", { widths: mobileFilterWidths });
  await shot(page, "mobile-archivio.png");
  await layout(page, "mobile archive");
  assert(!(await page.locator(".sidebar").isVisible()));
  await page.getByRole("button", { name: "Apri la navigazione" }).click();
  assert(await page.locator(".sidebar").isVisible());
  assert.equal(await page.getByRole("button", { name: "Apri la navigazione" }).getAttribute("aria-expanded"), "true");
  await page.keyboard.press("Escape");
  assert(!(await page.locator(".sidebar").isVisible()));
  pass("mobile menu and Escape");
  for (const [pathname, name] of [[productionPath, "produzione"], ["/calendario/", "calendario"], ["/richieste/", "richieste"], ["/guida/", "guida"], ["/nuovo/", "deposito"]]) {
    await visit(page, pathname);
    if (await page.locator("video").count()) {
      await page.waitForFunction(() => document.querySelector("video").readyState >= 2);
      await page.locator("video").evaluate(async (video) => { await video.play(); video.pause(); });
      await page.waitForFunction(() => document.querySelector("video").readyState >= 3);
    }
    await layout(page, `mobile ${name}`);
    await shot(page, `mobile-${name}.png`);
  }
  await page.setViewportSize({ width: 320, height: 700 });
  await visit(page, "/");
  await layout(page, "small mobile archive");
  const smallFilterWidths = await page.locator(".filter-bar select").evaluateAll((items) => items.map((item) => Math.round(item.getBoundingClientRect().width)));
  assert(smallFilterWidths.every((width) => width >= 110));
  pass("small mobile filter labels", { widths: smallFilterWidths });
  // No demo approval, publication record, upload or external generator is modified.
  await browser.close();
  browser = null;
  assert.equal(report.errors.length, 0, "Browser console errors");
  report.status = "PASS";
})().catch((error) => {
  report.status = "FAIL";
  report.failure = String(error.message);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, checks: report.checks.length, screenshots: report.screenshots.length, failure: report.failure || null }));
});
