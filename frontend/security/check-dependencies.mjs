import { readFile } from "node:fs/promises";

const lock = await readFile(new URL("../pnpm-lock.yaml", import.meta.url), "utf8");
const blocked = ["picomatch", "micromatch", "tinyglobby", "@parcel/watcher"];
for (const name of blocked) {
  if (lock.includes(`${name}@`)) throw new Error(`Blocked dependency remains: ${name}`);
}
console.log("Known affected matcher dependency chains are absent. Run pnpm audit for the current advisory database.");
