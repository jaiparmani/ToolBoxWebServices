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

  if (!res.ok) {
    throw new Error(
      `PA API ${options.method || "GET"} ${path} failed: ${res.status} ${await res.text()}`
    );
  }

  return res.status === 204 ? null : res.json();
}

// PA's REST API can create a console record, but it doesn't actually start
// the underlying process until something loads the console page in a
// browser (an undocumented quirk - see https://help.pythonanywhere.com/pages/API/,
// which only says "does not actually start the process. Only connecting to
// the console in a browser will do that"). We tried driving the console's
// terminal purely through Playwright keystrokes/DOM-reads too, but its
// output never showed up via innerText - PA's terminal here renders to a
// canvas, not text DOM nodes, so there's nothing to read back that way. The
// REST API's send_input/get_latest_output *do* return real text (confirmed
// working end-to-end once), so Playwright's only job is to log in and open
// the console page long enough to wake the process; the actual command and
// its output go through the API.
async function wakeConsole(consoleUrl) {
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

    await page.goto(`https://www.pythonanywhere.com${consoleUrl}`, {
      waitUntil: "networkidle",
    });

    // give the console's websocket a moment to actually spin up the process
    await page.waitForTimeout(5000);
  } finally {
    await browser.close();
  }
}

async function gitPull() {
  console.log(`Opening console in ${PA_WORKING_DIR}...`);
  const console_ = await paApi("consoles/", {
    method: "POST",
    body: JSON.stringify({
      executable: "bash",
      arguments: "",
      working_directory: PA_WORKING_DIR,
    }),
  });

  try {
    console.log(`Console ${console_.id} created, waking it...`);
    await wakeConsole(console_.console_url || `/user/${PA_USERNAME}/consoles/${console_.id}/`);

    await paApi(`consoles/${console_.id}/send_input/`, {
      method: "POST",
      body: JSON.stringify({ input: "git pull\n" }),
    });

    let output = "";
    for (let i = 0; i < 5; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const chunk = await paApi(`consoles/${console_.id}/get_latest_output/`);
      output += chunk.output;
    }

    console.log("git pull output:\n" + output);
  } finally {
    await paApi(`consoles/${console_.id}/`, { method: "DELETE" });
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
