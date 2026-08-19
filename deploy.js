const PA_USERNAME = process.env.PA_USERNAME;
const PA_API_TOKEN = process.env.PA_API_TOKEN;
const PA_WORKING_DIR = process.env.PA_WORKING_DIR || "/home/toolbox/ToolBoxWebServices";
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
