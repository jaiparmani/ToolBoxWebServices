const { chromium } = require("playwright");

const PA_USERNAME = process.env.PA_USERNAME;
const PA_PASSWORD = process.env.PA_PASSWORD;
const PA_WORKING_DIR = process.env.PA_WORKING_DIR || "/home/toolbox/toolboxservices";
const PA_DOMAIN = process.env.PA_DOMAIN || "toolbox.pythonanywhere.com";

// PythonAnywhere's REST API can create a console record, but it doesn't
// actually start the underlying process until something loads the console
// page in a browser (an undocumented quirk - see
// https://help.pythonanywhere.com/pages/API/, which only says "does not
// actually start the process. Only connecting to the console in a browser
// will do that"). Its webapp reload API endpoint also started returning a
// 500 for reasons we couldn't diagnose. Rather than fight either of those,
// this drives PA's own web UI end-to-end with Playwright, exactly the way a
// human (and the existing renew.js script) would.
//
// IMPORTANT CAVEAT: written without a real logged-in PA session to verify
// selectors against, so the "start a new console" control and the xterm.js
// terminal selectors below are best-effort guesses with defensive
// fallbacks. Every major step logs page state (URL, title, body text
// snippets) so a failure in real CI is debuggable from the logs and the
// error screenshot rather than being a silent black box.

// Try a list of candidate locators in order; click the first one that
// actually matches something on the page. Returns true if something was
// clicked.
async function clickFirstMatch(page, locatorFns, description) {
  for (const makeLocator of locatorFns) {
    try {
      const locator = makeLocator();
      const count = await locator.count();
      if (count > 0) {
        console.log(
          `[gitPull] Found ${description} (${count} match${count === 1 ? "" : "es"}); clicking the first one.`
        );
        await locator.first().click({ timeout: 10000 });
        return true;
      }
    } catch (err) {
      console.log(`[gitPull] Selector attempt for ${description} failed: ${err.message}`);
    }
  }
  return false;
}

// The console terminal may render directly on the page or inside an
// iframe, and PA may use xterm.js (modern) or an older term.js-style
// container. Search the main page and all frames for anything plausible.
async function findTerminalHandle(page) {
  const containerSelectors = [
    ".xterm",
    ".xterm-screen",
    "#terminal",
    ".terminal",
    "[id^='terminal-']",
    "[id^='id_terminal']",
    "[class*='terminal']",
  ];

  for (const frame of [page, ...page.frames()]) {
    for (const sel of containerSelectors) {
      try {
        const locator = frame.locator(sel);
        if ((await locator.count()) > 0) {
          return { frame, locator: locator.first(), selector: sel };
        }
      } catch (err) {
        // Frame may be detached/cross-origin/mid-navigation - keep trying.
      }
    }
  }
  return null;
}

// Read back whatever text xterm.js (or a fallback terminal container) has
// rendered, trying a few plausible selectors.
async function readTerminalText(frame) {
  const textSelectors = [
    ".xterm-rows",
    ".xterm-screen",
    ".xterm",
    "#terminal",
    ".terminal",
    "[id^='terminal-']",
    "[id^='id_terminal']",
  ];
  for (const sel of textSelectors) {
    try {
      const locator = frame.locator(sel);
      if ((await locator.count()) > 0) {
        const text = await locator.first().innerText();
        if (text && text.trim().length > 0) return text;
      }
    } catch (err) {
      // keep trying other selectors
    }
  }
  return "";
}

async function gitPull() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  let activePage = page;

  try {
    // 1. Log in (identical pattern to renew.js).
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

    // 2. Go to the consoles page.
    const consolesUrl = `https://www.pythonanywhere.com/user/${PA_USERNAME}/consoles/`;
    await page.goto(consolesUrl, { waitUntil: "networkidle" });
    console.log(`[gitPull] On consoles page. URL: ${page.url()}, title: "${await page.title()}"`);

    // 3. Start a new Bash console. PA may either navigate the current page
    // to the new console, or open it in a new tab - watch for both.
    const popupPromise = page
      .context()
      .waitForEvent("page", { timeout: 8000 })
      .catch(() => null);

    const startedBash = await clickFirstMatch(
      page,
      [
        () => page.getByRole("link", { name: "Bash", exact: true }),
        () => page.locator("a", { hasText: /^\s*Bash\s*$/ }),
        () => page.locator("#id_new_console_form a:has-text('Bash')"),
        () => page.locator(".new-console-list a:has-text('Bash')"),
        () => page.locator("a[href*='bash']"),
        () => page.locator("text=Bash"),
      ],
      '"Bash" start-console control'
    );

    if (!startedBash) {
      console.log("[gitPull] Could not find a 'Bash' start-console control. Dumping page text for debugging:");
      console.log((await page.innerText("body")).slice(0, 3000));
      throw new Error("Could not locate the 'Bash' start-console control on the consoles page.");
    }

    const popup = await popupPromise;
    if (popup) {
      console.log("[gitPull] Bash console opened in a new tab/window; switching to it.");
      await popup.waitForLoadState("networkidle").catch((err) => {
        console.log(`[gitPull] New tab didn't reach networkidle cleanly: ${err.message}`);
      });
      activePage = popup;
    } else {
      // Same-tab flow: starting the console either triggers a full
      // navigation, or updates the page via AJAX and redirects shortly
      // after. Give it a generous window to settle either way.
      try {
        await page.waitForNavigation({ waitUntil: "networkidle", timeout: 20000 });
      } catch (err) {
        console.log(
          `[gitPull] No full navigation detected after clicking Bash (${err.message}); continuing - console may render in place.`
        );
      }
      for (let i = 0; i < 5 && !/\/consoles\/\d+/.test(page.url()); i++) {
        await page.waitForTimeout(1000);
      }
      activePage = page;
    }

    console.log(`[gitPull] After starting console. URL: ${activePage.url()}, title: "${await activePage.title()}"`);

    // 4. Find the terminal and wait for it to become interactive.
    let terminal = null;
    for (let attempt = 0; attempt < 10 && !terminal; attempt++) {
      terminal = await findTerminalHandle(activePage);
      if (!terminal) await activePage.waitForTimeout(1000);
    }

    if (!terminal) {
      console.log("[gitPull] Could not locate a terminal element. Dumping page text for debugging:");
      console.log((await activePage.innerText("body")).slice(0, 3000));
      throw new Error("Could not find the console terminal element on the page.");
    }

    console.log(`[gitPull] Terminal located via selector "${terminal.selector}".`);

    // PA quirk: the console process only actually starts once the console
    // page has loaded in a browser, and the shell prompt can take a couple
    // of seconds to appear over the websocket - give it room before typing.
    await activePage.waitForTimeout(5000);

    await terminal.locator.click({ timeout: 10000 });
    await activePage.waitForTimeout(500);

    // 5. Type the command as real keystrokes. xterm.js has no form input -
    // it captures keyboard events on a focused hidden textarea, so
    // page.keyboard.type() after focusing the terminal is the standard way
    // to drive it (page.fill()/page.type() on a selector won't work here).
    const command = `cd ${PA_WORKING_DIR} && git pull\n`;
    console.log(`[gitPull] Typing command: ${command.trim()}`);
    await activePage.keyboard.type(command, { delay: 50 });

    // 6. Wait for git pull to finish over the PA network, then capture output.
    await activePage.waitForTimeout(8000);

    const output = await readTerminalText(terminal.frame);
    console.log("[gitPull] Captured terminal output:\n" + (output || "(empty)"));

    if (!output || output.trim().length === 0) {
      throw new Error("Captured empty terminal output after running git pull - cannot confirm the command ran.");
    }

    const lowerOutput = output.toLowerCase();
    const errorSignals = [
      "not a git repository",
      "permission denied",
      "command not found",
      "fatal:",
      "could not resolve host",
      "authentication failed",
    ];
    const hitError = errorSignals.find((signal) => lowerOutput.includes(signal));
    if (hitError) {
      throw new Error(`git pull output contains an error signal ("${hitError}"). Full output logged above.`);
    }

    console.log("[gitPull] git pull appears to have completed successfully.");
  } catch (err) {
    console.error("[gitPull] Failed:", err);
    try {
      await activePage.screenshot({ path: "git-pull-error.png", fullPage: true });
      console.log("[gitPull] Saved screenshot to git-pull-error.png");
    } catch (screenshotErr) {
      console.error("[gitPull] Additionally failed to capture error screenshot:", screenshotErr);
    }
    throw err;
  } finally {
    await browser.close();
  }
}

async function reloadWebapp() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    // Login
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

    // Open web app page
    await page.goto(
      `https://www.pythonanywhere.com/user/${PA_USERNAME}/webapps/`,
      { waitUntil: "networkidle" }
    );

    // Click the Reload button. PA renders these controls as
    // <input type="submit" value="..."> (confirmed for the renewal button
    // in renew.js: input[value="Run until 1 month from today"]), and the
    // reload control is expected to read like "Reload <domain>". Try the
    // exact/expected form first, then fall back to progressively looser,
    // case-insensitive matches in case PA's markup differs from what we
    // guessed.
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

    // Give the reload request time to submit and the page to settle.
    await page.waitForLoadState("networkidle", { timeout: 30000 });

    console.log(`✅ Reloaded ${PA_DOMAIN}`);
  } catch (err) {
    console.error(err);

    // Save screenshot for debugging
    await page.screenshot({
      path: "reload-error.png",
      fullPage: true,
    });

    throw err;
  } finally {
    await browser.close();
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
