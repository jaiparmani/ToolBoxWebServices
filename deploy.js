const { chromium } = require("playwright");

const PA_USERNAME = process.env.PA_USERNAME;
const PA_PASSWORD = process.env.PA_PASSWORD;
const PA_API_TOKEN = process.env.PA_API_TOKEN;
const PA_WORKING_DIR = process.env.PA_WORKING_DIR || "/home/toolbox/toolboxservices";
const PA_DOMAIN = process.env.PA_DOMAIN || "toolbox.pythonanywhere.com";

const API_BASE = `https://www.pythonanywhere.com/api/v0/user/${PA_USERNAME}/`;

async function paApi(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Token ${PA_API_TOKEN}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (res.status === 204) return null;

  const contentType = res.headers.get("content-type") || "";
  if (!res.ok || !contentType.includes("application/json")) {
    const bodySnippet = (await res.text()).slice(0, 300);
    throw new Error(
      `PA API ${options.method || "GET"} ${path} failed: ${res.status} (content-type: ${contentType || "none"}) ${bodySnippet}`
    );
  }

  return res.json();
}

// The REST API's consoles/ POST (create-a-console) started consistently
// returning a 200 with PA's marketing homepage HTML instead of JSON -
// almost certainly some rate-limit/redirect tripped by how many
// logins/API calls this pipeline has made in a short window today.
// Browser-based console creation (clicking "Bash" on the consoles page,
// like a human) kept working throughout, though - and separately, its
// terminal renders to canvas, so there's no DOM text to read output back
// from. So: create/start the console via the browser (proven reliable),
// then drive send_input/get_latest_output/delete through the REST API for
// that existing console id (also proven reliable) - avoiding the one API
// call that's currently broken while still getting real, readable output.
async function startConsoleViaBrowser() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await page.goto("https://www.pythonanywhere.com/login/", {
      waitUntil: "networkidle",
    });

    await page.fill('input[name="auth-username"]', PA_USERNAME);
    await page.fill('input[name="auth-password"]', PA_PASSWORD);

    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.click('button[type="submit"]'),
    ]);

    console.log(`[gitPull] Logged in. Page title: "${await page.title()}"`);

    await page.goto(`https://www.pythonanywhere.com/user/${PA_USERNAME}/consoles/`, {
      waitUntil: "networkidle",
    });

    console.log(`[gitPull] On consoles page. URL: ${page.url()}, title: "${await page.title()}"`);

    // The "start a new console" Bash link's text is "$ Bash" (shell-prompt
    // style), not plain "Bash" - a plain "Bash" match instead lands on a
    // "Recent Consoles" entry like "Bash console 47845718" (reusing an old,
    // long-lived console instead of starting a fresh one, which we
    // previously hit). Try the "$ Bash" form first, then looser fallbacks,
    // but always reject any match containing a digit (a console id) so we
    // never accidentally reattach to a Recent Consoles entry.
    const bashCandidates = [
      page.getByRole("link", { name: "$ Bash" }),
      page.locator("a", { hasText: /^\$?\s*Bash\s*$/ }),
      page.locator("a:has-text('Bash')"),
    ];

    let clicked = false;
    for (const candidate of bashCandidates) {
      const count = await candidate.count();
      for (let i = 0; i < count; i++) {
        const locator = candidate.nth(i);
        try {
          const text = (await locator.innerText()).trim();
          if (/\d/.test(text)) continue; // skip Recent Consoles entries
          await locator.waitFor({ state: "visible", timeout: 8000 });
          await locator.click();
          clicked = true;
          break;
        } catch (err) {
          console.log(`[gitPull] Bash selector attempt failed: ${err.message}`);
        }
      }
      if (clicked) break;
    }

    if (!clicked) {
      console.log("[gitPull] Could not find a 'Bash' start-console link. Dumping page text for debugging:");
      console.log((await page.innerText("body")).slice(0, 3000));
      throw new Error("Could not locate the 'Bash' start-console control on the consoles page.");
    }

    for (let i = 0; i < 15 && !/\/consoles\/\d+/.test(page.url()); i++) {
      await page.waitForTimeout(1000);
    }

    const match = page.url().match(/\/consoles\/(\d+)/);
    if (!match) {
      throw new Error(`Console page URL never showed a console id: ${page.url()}`);
    }

    // give the console's websocket a moment to actually spin up the process
    await page.waitForTimeout(5000);

    return match[1];
  } catch (err) {
    await page.screenshot({ path: "git-pull-error.png", fullPage: true });
    throw err;
  } finally {
    await browser.close();
  }
}

async function gitPull() {
  console.log(`Starting console via browser for ${PA_WORKING_DIR}...`);
  const consoleId = await startConsoleViaBrowser();
  console.log(`Console ${consoleId} started.`);

  try {
    await paApi(`consoles/${consoleId}/send_input/`, {
      method: "POST",
      body: JSON.stringify({ input: `cd ${PA_WORKING_DIR} && git pull\n` }),
    });

    let output = "";
    for (let i = 0; i < 5; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const chunk = await paApi(`consoles/${consoleId}/get_latest_output/`);
      output += chunk.output;
    }

    console.log("git pull output:\n" + output);
  } finally {
    await paApi(`consoles/${consoleId}/`, { method: "DELETE" });
  }
}

// Click the Reload button on PA's webapps page. Kept as a fallback in case
// the REST API reload call (tried first in reloadWebapp()) ever misbehaves
// again - it previously 500'd, but that turned out to be because it was
// targeting a domain this account doesn't own, not an API problem.
async function reloadWebappViaBrowser() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await page.goto("https://www.pythonanywhere.com/login/", {
      waitUntil: "networkidle",
    });

    await page.fill('input[name="auth-username"]', PA_USERNAME);
    await page.fill('input[name="auth-password"]', PA_PASSWORD);

    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.click('button[type="submit"]'),
    ]);

    console.log("✅ Logged in");

    await page.goto(`https://www.pythonanywhere.com/user/${PA_USERNAME}/webapps/`, {
      waitUntil: "networkidle",
    });

    const reloadSelectors = [
      `input[value="Reload ${PA_DOMAIN}"]`,
      'input[value^="Reload "]',
      'input[value*="Reload" i]',
      'button:has-text("Reload")',
      'text=/Reload/i',
    ];

    let clicked = false;
    let lastErr;
    for (const selector of reloadSelectors) {
      try {
        const locator = page.locator(selector).first();
        await locator.waitFor({ state: "visible", timeout: 5000 });
        await locator.click();
        clicked = true;
        break;
      } catch (err) {
        lastErr = err;
      }
    }

    if (!clicked) {
      throw new Error(
        `Could not find a visible Reload button on the webapps page for ${PA_DOMAIN}: ${lastErr}`
      );
    }

    await page.waitForLoadState("networkidle", { timeout: 30000 });
    console.log(`✅ Reloaded ${PA_DOMAIN} (via browser click)`);
  } catch (err) {
    await page.screenshot({ path: "reload-error.png", fullPage: true });
    throw err;
  } finally {
    await browser.close();
  }
}

async function reloadWebapp() {
  try {
    await paApi(`webapps/${PA_DOMAIN}/reload/`, { method: "POST" });
    console.log(`✅ Reloaded ${PA_DOMAIN} (via API)`);
  } catch (err) {
    console.log(`Reload API failed (${err.message}); falling back to clicking the button directly.`);
    await reloadWebappViaBrowser();
  }
}

(async () => {
  try {
    await gitPull();
    await reloadWebapp();
  } catch (err) {
    console.error(err);
    process.exit(1);
  }
})();
