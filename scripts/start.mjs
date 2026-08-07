#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, "..");
const options = parseArguments(process.argv.slice(2));

if (options.help) {
  printHelp();
  process.exit(0);
}

if (options.port) process.env.PORT = options.port;
if (options.host) process.env.HOST = options.host;
process.env.NODE_ENV = "production";
process.env.REPOMENTOR_ASSET_ROOT = projectRoot;

const productionBuildReady = hasProductionBuild(projectRoot);
if ((options.rebuild || !productionBuildReady) && !hasBuildSources(projectRoot)) {
  console.error(
    productionBuildReady
      ? "--rebuild is available from a source checkout, not from a packaged installation."
      : "RepoMentor production assets are missing. Reinstall the package or run from a source checkout.",
  );
  process.exit(1);
}

if (options.rebuild || !productionBuildReady) {
  console.log("RepoMentor production assets are not ready; building them now...");
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(npmCommand, ["run", "build"], {
    cwd: projectRoot,
    stdio: "inherit",
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const host = process.env.HOST ?? "127.0.0.1";
const browserHost = host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
const url = `http://${browserHost}:${process.env.PORT ?? "3000"}`;
console.log(`Starting RepoMentor at ${url}`);

if (!options.noOpen && process.env.CI !== "true") {
  void openWhenReady(url);
}

await import(pathToFileURL(path.join(projectRoot, "dist", "index.js")).href);

function hasProductionBuild(root) {
  return fs.existsSync(path.join(root, "dist", "index.js"))
    && fs.existsSync(path.join(root, "web", "dist", "index.html"));
}

function hasBuildSources(root) {
  return fs.existsSync(path.join(root, "src", "index.ts"))
    && fs.existsSync(path.join(root, "web", "src"));
}

function parseArguments(args) {
  const parsed = { help: false, noOpen: false, rebuild: false, port: "", host: "" };
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--help" || argument === "-h") parsed.help = true;
    else if (argument === "--no-open") parsed.noOpen = true;
    else if (argument === "--rebuild") parsed.rebuild = true;
    else if (argument === "--port") parsed.port = requireValue(args, ++index, "--port");
    else if (argument === "--host") parsed.host = requireValue(args, ++index, "--host");
    else {
      console.error(`Unknown option: ${argument}`);
      printHelp();
      process.exit(1);
    }
  }
  if (parsed.port && (!/^\d+$/.test(parsed.port) || Number(parsed.port) < 1 || Number(parsed.port) > 65535)) {
    console.error("--port must be an integer between 1 and 65535");
    process.exit(1);
  }
  return parsed;
}

function requireValue(args, index, option) {
  const value = args[index];
  if (!value || value.startsWith("--")) {
    console.error(`${option} requires a value`);
    process.exit(1);
  }
  return value;
}

function printHelp() {
  console.log(`RepoMentor local repository mentor

Usage:
  repomentor [options]
  npm start -- [options]

Options:
  --port <number>  Local server port (default: 3000)
  --host <address> Bind address (default: 127.0.0.1)
  --no-open        Do not open the browser automatically
  --rebuild        Rebuild frontend and backend before starting
  -h, --help       Show this help`);
}

async function openWhenReady(targetUrl) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    try {
      const response = await fetch(`${targetUrl}/health`);
      if (response.ok) {
        openBrowser(targetUrl);
        return;
      }
    } catch {
      // The local server is still starting.
    }
  }
  console.warn(`RepoMentor started, but the browser was not opened. Visit ${targetUrl}`);
}

function openBrowser(targetUrl) {
  const command = process.platform === "win32"
    ? ["rundll32", ["url.dll,FileProtocolHandler", targetUrl]]
    : process.platform === "darwin"
      ? ["open", [targetUrl]]
      : ["xdg-open", [targetUrl]];
  try {
    const child = spawn(command[0], command[1], {
      detached: true,
      stdio: "ignore",
    });
    child.unref();
  } catch {
    console.warn(`Open ${targetUrl} in your browser.`);
  }
}
