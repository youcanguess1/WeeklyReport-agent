import { build } from "esbuild";
import { readdir, mkdtemp, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
async function findTests(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => entry.isDirectory()
    ? findTests(path.join(directory, entry.name))
    : entry.name.endsWith(".test.tsx") ? [path.join(directory, entry.name)] : []));
  return nested.flat();
}
const directory = await mkdtemp(path.join(root, "node_modules/.weeklyreport-tests-"));
try {
  const tests = await findTests(path.join(root, "src"));
  await build({ entryPoints: tests, outdir: directory, outbase: path.join(root, "src"),
    outExtension: { ".js": ".mjs" }, bundle: true, platform: "node", format: "esm",
    packages: "external", jsx: "automatic", target: "node24", logLevel: "warning" });
  const compiled = tests.map((filename) => path.join(directory, path.relative(path.join(root, "src"), filename)).replace(/\.tsx$/, ".mjs"));
  const child = spawn(process.execPath, ["--import", pathToFileURL(path.join(root, "src/test/dom.mjs")).href,
    "--test", "--test-concurrency=1", "--test-reporter=spec", ...compiled,
    path.join(root, "scripts/serve.test.mjs")], { stdio: "inherit", cwd: root });
  process.exitCode = await new Promise((resolve, reject) => { child.on("error", reject); child.on("exit", (code) => resolve(code ?? 1)); });
} finally {
  await rm(directory, { recursive: true, force: true });
}
