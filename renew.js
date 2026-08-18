const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
  });

  const page = await browser.newPage();

  try {
    // Login
    await page.goto("https://www.pythonanywhere.com/login/", {
      waitUntil: "networkidle",
    });

    await page.fill('input[name="auth-username"]', process.env.PA_USERNAME);
    await page.fill('input[name="auth-password"]', process.env.PA_PASSWORD);

    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.click('button[type="submit"]'),
    ]);

    console.log("✅ Logged in");

    // Open web app page
    await page.goto(
      "https://www.pythonanywhere.com/user/toolbox/webapps/",
      {
        waitUntil: "networkidle",
      }
    );

    // Click the renewal button
    await page.locator('input[value="Run until 1 month from today"]').click();

    console.log("✅ Website renewed successfully");
  } catch (err) {
    console.error(err);

    // Save screenshot for debugging
    await page.screenshot({
      path: "renew-error.png",
      fullPage: true,
    });

    process.exit(1);
  } finally {
    await browser.close();
  }
})();
