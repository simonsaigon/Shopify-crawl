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

function saveUploadedFile(fileName, content) {
  fs.mkdirSync(UPLOAD_DIR, { recursive: true });
  const safeName = `${Date.now()}-${path.basename(fileName || "urls.txt")}`;
  const fullPath = path.join(UPLOAD_DIR, safeName);
  fs.writeFileSync(fullPath, content, "utf8");
  return path.join("uploads", safeName);
}

function buildArgs(body) {
  const args = ["shopifycrawl.py", "-o", "output"];
  const mode = body.mode;

  if (mode === "url") {
    if (!body.url) throw new Error("url is required");
    args.push("-u", body.url);
  } else if (mode === "category") {
    if (!body.category) throw new Error("category URL is required");
    args.push("-c", body.category);
  } else if (mode === "categories") {
    args.push("--category-file", "categories.txt");
  } else if (mode === "file") {
    if (!body.fileContent) throw new Error("A .txt file of URLs is required");
    const relPath = saveUploadedFile(body.fileName, body.fileContent);
    args.push("-f", relPath);
  } else {
    throw new Error("Invalid mode");
  }

  if (body.noReviews) args.push("--no-reviews");
  if (body.noSelenium) args.push("--no-selenium");
  if (body.maxReviewPages) args.push("--max-review-pages", String(parseInt(body.maxReviewPages, 10)));
  if (body.delay) args.push("--delay", String(parseInt(body.delay, 10)));

  return args;
}

app.post("/api/scrape", (req, res) => {
  let args;
  try {
    args = buildArgs(req.body);
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

  const child = spawn("python", args, { cwd: ROOT });

  child.stdout.on("data", (chunk) => send("log", chunk.toString()));
  child.stderr.on("data", (chunk) => send("log", chunk.toString()));

  child.on("error", (err) => {
    send("log", `Failed to start scraper: ${err.message}\n`);
    send("done", { code: -1 });
    res.end();
  });

  child.on("close", (code) => {
    send("done", { code });
    res.end();
  });

  req.on("close", () => {
    if (!child.killed) child.kill();
  });
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
