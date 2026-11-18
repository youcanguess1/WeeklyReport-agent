import { it } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createAppServer } from "./serve.mjs";

it("serves route refreshes and streams multipart API requests and response headers", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "weeklyreport-server-"));
  const upstream = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    response.writeHead(201, { "Content-Type": "application/json", "X-Upload": "received" });
    response.end(JSON.stringify({ path: request.url, method: request.method,
      contentType: request.headers["content-type"], body: Buffer.concat(chunks).toString() }));
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const server = createAppServer({ directory, backend: `http://127.0.0.1:${upstream.address().port}` });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await writeFile(path.join(directory, "index.html"), "<title>WeeklyReport</title>");
    assert.equal(await (await fetch(`${base}/tasks/example/trace`)).text(), "<title>WeeklyReport</title>");
    assert.equal((await fetch(`${base}/missing.js`)).status, 404);
    const body = "--sample\r\nContent-Disposition: form-data; name=\"notes\"\r\n\r\n周报记录\r\n--sample--\r\n";
    const result = await fetch(`${base}/api/tasks?upload=1`, { method: "POST",
      headers: { "Content-Type": "multipart/form-data; boundary=sample" }, body });
    assert.equal(result.status, 201);
    assert.equal(result.headers.get("x-upload"), "received");
    assert.deepEqual(await result.json(), { path: "/api/tasks?upload=1", method: "POST",
      contentType: "multipart/form-data; boundary=sample", body });
    const traversal = await fetch(`${base}/%2e%2e%5cpackage.json`);
    assert.equal(traversal.status, 403);
  } finally {
    await Promise.all([new Promise((resolve) => server.close(resolve)), new Promise((resolve) => upstream.close(resolve))]);
    await rm(directory, { recursive: true, force: true });
  }
});
