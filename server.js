const express = require("express");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");

const app = express();
const PORT = process.env.PORT || 3000;
const ROOT = __dirname;
const OUTPUT_DIR = path.join(ROOT, "output");
const UPLOAD_DIR = path.join(ROOT, "uploads");

app.use(express.json({ limit: "5mb" }));
app.use(express.static(path.join(ROOT, "public")));

function saveTempFile(fileName, content) {
  fs.mkdirSync(UPLOAD_DIR, { recursive: true });
  const safeName = `${Date.now()}-${path.basename(fileName || "urls.txt")}`;
  const fullPath = path.join(UPLOAD_DIR, safeName);
  fs.writeFileSync(fullPath, content, "utf8");
  return path.join("uploads", safeName);
}

function isCategoryUrl(url) {
  return url.includes("/categories/");
}

function commonArgs(body) {
  const args = [];
  if (body.noReviews) args.push("--no-reviews");
  if (body.noSelenium) args.push("--no-selenium");
  if (body.maxReviewPages) args.push("--max-review-pages", String(parseInt(body.maxReviewPages, 10)));
  if (body.delay) args.push("--delay", String(parseInt(body.delay, 10)));
  return args;
}

// Builds one or more independent scrape jobs (each an argv array) from the request body.
// mode "url": pastes of one or many lines, auto-detected per line as an app URL or a
// category URL (anything containing "/categories/"). mode "file": an uploaded .txt of app URLs.
function buildJobs(body) {
  const base = ["shopifycrawl.py", "-o", "output"];
  const extra = commonArgs(body);
  const jobs = [];

  if (body.mode === "file") {
    if (!body.fileContent) throw new Error("A .txt file of URLs is required");
    const relPath = saveTempFile(body.fileName, body.fileContent);
    jobs.push([...base, "-f", relPath, ...extra]);
    return jobs;
  }

  if (body.mode !== "url") throw new Error("Invalid mode");

  const lines = (body.urls || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  if (lines.length === 0) throw new Error("Paste at least one app or category URL");

  const categoryUrls = lines.filter(isCategoryUrl);
  const appUrls = lines.filter((l) => !isCategoryUrl(l));

  if (categoryUrls.length === 1) {
    jobs.push([...base, "-c", categoryUrls[0], ...extra]);
  } else if (categoryUrls.length > 1) {
    const relPath = saveTempFile("categories.txt", categoryUrls.join("\n"));
    jobs.push([...base, "--category-file", relPath, ...extra]);
  }

  if (appUrls.length === 1) {
    jobs.push([...base, "-u", appUrls[0], ...extra]);
  } else if (appUrls.length > 1) {
    const relPath = saveTempFile("apps.txt", appUrls.join("\n"));
    jobs.push([...base, "-f", relPath, ...extra]);
  }

  return jobs;
}

function runJob(args, send, onChild) {
  return new Promise((resolve) => {
    const child = spawn("python", args, { cwd: ROOT });
    onChild(child);

    child.stdout.on("data", (chunk) => send("log", chunk.toString()));
    child.stderr.on("data", (chunk) => send("log", chunk.toString()));

    child.on("error", (err) => {
      send("log", `Failed to start scraper: ${err.message}\n`);
      resolve(-1);
    });

    child.on("close", (code) => resolve(code));
  });
}

app.post("/api/scrape", async (req, res) => {
  let jobs;
  try {
    jobs = buildJobs(req.body);
  } catch (err) {
    res.status(400).json({ error: err.message });
    return;
  }

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (event, data) => {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  let currentChild = null;
  req.on("close", () => {
    if (currentChild && !currentChild.killed) currentChild.kill();
  });

  let lastCode = 0;
  for (const args of jobs) {
    lastCode = await runJob(args, send, (child) => {
      currentChild = child;
    });
    if (res.writableEnded) break;
  }

  send("done", { code: lastCode });
  res.end();
});

app.get("/api/files", (req, res) => {
  const files = [];
  const walk = (dir, rel) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const relPath = path.join(rel, entry.name);
      if (entry.isDirectory()) {
        walk(path.join(dir, entry.name), relPath);
      } else {
        files.push(relPath.replace(/\\/g, "/"));
      }
    }
  };
  walk(OUTPUT_DIR, "");
  res.json({ files });
});

app.get("/api/download", (req, res) => {
  const rel = req.query.file;
  if (!rel || typeof rel !== "string") {
    res.status(400).send("Missing file parameter");
    return;
  }
  const resolved = path.resolve(OUTPUT_DIR, rel);
  if (!resolved.startsWith(OUTPUT_DIR + path.sep)) {
    res.status(400).send("Invalid file path");
    return;
  }
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    res.status(404).send("File not found");
    return;
  }
  res.download(resolved);
});

app.listen(PORT, () => {
  console.log(`Shopify Crawl web UI running at http://localhost:${PORT}`);
});
