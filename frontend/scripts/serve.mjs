import http from "node:http";
import https from "node:https";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { context } from "esbuild";
import { buildOptions, root } from "./build.mjs";

const mediaTypes = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
};

export function createAppServer({ directory = path.join(root, "dist"),
  backend = process.env.WEEKLYREPORT_BACKEND_URL ?? "http://127.0.0.1:8000" } = {}) {
  const backendUrl = new URL(backend);
  if (!["http:", "https:"].includes(backendUrl.protocol)) throw new Error("Backend URL must use HTTP or HTTPS");
  return http.createServer(async (request, response) => {
    const pathname = new URL(request.url, "http://localhost").pathname;
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      const transport = backendUrl.protocol === "https:" ? https : http;
      const upstream = transport.request({
        protocol: backendUrl.protocol, hostname: backendUrl.hostname, port: backendUrl.port,
        method: request.method, path: request.url,
        headers: { ...request.headers, host: backendUrl.host },
      }, (incoming) => {
        response.writeHead(incoming.statusCode ?? 502, incoming.headers);
        incoming.pipe(response);
      });
      upstream.on("error", () => {
        if (!response.headersSent) response.writeHead(502, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Backend unavailable" }));
      });
      request.on("aborted", () => upstream.destroy());
      request.pipe(upstream);
      return;
    }
    if (!["GET", "HEAD"].includes(request.method)) {
      response.writeHead(405); response.end(); return;
    }
    try {
      const decoded = decodeURIComponent(pathname);
      const relative = decoded.replace(/^\/+/, "");
      let filename = path.resolve(directory, relative || "index.html");
      if (!filename.startsWith(path.resolve(directory) + path.sep)) {
        response.writeHead(403); response.end(); return;
      }
      let info = await stat(filename).catch(() => null);
      if (!info?.isFile() && !path.extname(relative)) {
        filename = path.join(directory, "index.html");
        info = await stat(filename).catch(() => null);
      }
      if (!info?.isFile()) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, {
        "Content-Type": mediaTypes[path.extname(filename)] ?? "application/octet-stream",
        "Content-Length": info.size, "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
      });
      if (request.method === "HEAD") response.end();
      else createReadStream(filename).pipe(response);
    } catch {
      response.writeHead(400); response.end();
    }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const portIndex = args.indexOf("--port");
  const port = portIndex < 0 ? 5174 : Number(args[portIndex + 1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid port");
  let builder;
  if (!args.includes("--preview")) {
    builder = await context(buildOptions(true));
    await builder.rebuild();
    await builder.watch();
  }
  const server = createAppServer();
  server.on("error", async (error) => { console.error(error.message); await builder?.dispose(); process.exitCode = 1; });
  server.listen(port, "127.0.0.1", () => console.log(`WeeklyReport Agent: http://127.0.0.1:${port}`));
  const close = async () => { server.close(); await builder?.dispose(); };
  process.once("SIGINT", close);
  process.once("SIGTERM", close);
}
