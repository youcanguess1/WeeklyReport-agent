import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => entry.isDirectory()
    ? sourceFiles(path.join(directory, entry.name))
    : [path.join(directory, entry.name)]));
  return nested.flat();
}

export function buildOptions(development = false) {
  return {
    absWorkingDir: root,
    entryPoints: { app: "src/main.tsx" },
    bundle: true,
    outdir: "dist/assets",
    format: "esm",
    platform: "browser",
    target: ["chrome111", "firefox128", "safari16.4"],
    jsx: "automatic",
    minify: !development,
    sourcemap: development ? "inline" : false,
    define: { "process.env.NODE_ENV": JSON.stringify(development ? "development" : "production") },
    plugins: [{
      name: "tailwind-postcss",
      setup(builder) {
        builder.onLoad({ filter: /styles\.css$/ }, async (args) => {
          const css = await readFile(args.path, "utf8");
          const result = await postcss([tailwindcss({ base: root, optimize: !development })])
            .process(css, { from: args.path });
          return {
            contents: result.css,
            loader: "css",
            resolveDir: path.dirname(args.path),
            watchFiles: await sourceFiles(path.join(root, "src")),
          };
        });
        builder.onEnd(async (result) => {
          if (result.errors.length) return;
          const html = (await readFile(path.join(root, "index.html"), "utf8"))
            .replace('<script type="module" src="/src/main.tsx"></script>',
              '<link rel="stylesheet" href="/assets/app.css" />\n    <script type="module" src="/assets/app.js"></script>');
          await mkdir(path.join(root, "dist"), { recursive: true });
          await writeFile(path.join(root, "dist/index.html"), html);
        });
      },
    }],
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await build(buildOptions());
  console.log("Production build ready in dist/");
}
