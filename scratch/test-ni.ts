import { runStage } from "../src/services/claude-client.js";
import { cloneRepo } from "../src/lib/repo.js";
import path from "path";
import fs from "fs";

async function testNi() {
  const url = "https://github.com/antfu/ni";
  const taskDir = path.join(process.cwd(), "data/repos/test_antfu_ni");
  
  if (!fs.existsSync(taskDir)) {
    console.log("Cloning ni...");
    await cloneRepo(url, taskDir);
  }

  const callbacks = {
    onProgress: (m: string) => console.log(`[Progress] ${m}`),
    onField: () => {}
  };

  try {
    console.log("Running explorer stage...");
    const out = await runStage("explorer", {
      repoUrl: url,
      fileCount: 40 // approximate
    }, callbacks, taskDir);
    
    console.log("\n--- SUCCESS ---");
    console.log(JSON.stringify(out, null, 2));
  } catch (err: any) {
    console.error("\n--- ERROR ---");
    console.error(err.message);
    if (err.rawOutput) {
      console.log("\n--- RAW OUTPUT START ---");
      console.log(err.rawOutput);
      console.log("--- RAW OUTPUT END ---");
    }
  }
}

testNi().catch(console.error);
