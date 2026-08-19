const { chromium } = require("playwright");

const PA_USERNAME = process.env.PA_USERNAME;
const PA_PASSWORD = process.env.PA_PASSWORD;
const PA_API_TOKEN = process.env.PA_API_TOKEN;
const PA_WORKING_DIR = process.env.PA_WORKING_DIR || "/home/toolbox";
const PA_DOMAIN = process.env.PA_DOMAIN || "jaiparmani.pythonanywhere.com";

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

// PA only actually starts a console's process once something loads it in a
// browser (the API create call just allocates the record) - see
// https://help.pythonanywhere.com/pages/API/. So we log in with Playwright
// just long enough to open the console page and wake the process, then do
// the rest (send_input / get_latest_output / delete) over the plain API.
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

async function reloadWebapp() {
  const webapps = await paApi("webapps/");
  console.log(
    `Webapps visible to ${PA_USERNAME}: ${webapps.map((w) => w.domain_name).join(", ") || "(none)"}`
  );

  await paApi(`webapps/${PA_DOMAIN}/reload/`, { method: "POST" });
  console.log(`✅ Reloaded ${PA_DOMAIN}`);
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
