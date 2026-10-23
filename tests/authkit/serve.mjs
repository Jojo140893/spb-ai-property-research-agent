// Serve the generated middleware.js on https://app.smartpropertybuying.com.au:<port> LOCALLY, in front of
// the real Python functions and the fake Blob / SMTP / clock, so a headless browser can look at the pages.
// For screenshots and manual poking only (tests/authkit/screenshots.py drives it); the tests do not use it.
//
// It listens on 127.0.0.1 only, with a throwaway self-signed certificate (SPB_SERVE_CERT / SPB_SERVE_KEY),
// and the browser is told to resolve app.smartpropertybuying.com.au to 127.0.0.1 and to use no proxy, so
// the page's same-origin check (Origin == Host, https) behaves exactly as it will in production while
// nothing leaves the machine.
//
//   SPB_MW_PATH     the middleware to serve          SPB_SERVE_ENV  JSON of environment overrides
//                                                    (e.g. {"SPB_SMTP_PASSWORD": null} = "not set up yet")
// Prints one JSON line {"port": N} when ready; stops when stdin closes.
import fs from "node:fs";
import https from "node:https";
import { startWorld } from "./emulator.mjs";

const world = await startWorld({ mwPath: process.env.SPB_MW_PATH });
await world.fresh(JSON.parse(process.env.SPB_SERVE_ENV || "{}"));

const STUB = "<!doctype html><title>Dashboard</title><body style=\"font:15px sans-serif;padding:40px\">" +
  "<h1>Dashboard (stand-in)</h1><p>You are signed in.</p><p><a href=\"/logout\">Sign out</a></p>";

const server = https.createServer(
  { key: fs.readFileSync(process.env.SPB_SERVE_KEY), cert: fs.readFileSync(process.env.SPB_SERVE_CERT) },
  async (req, res) => {
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = Buffer.concat(chunks);
      const url = new URL(req.url, "https://" + req.headers.host);
      // Demo controls: the mailbox's last code and the fake clock. Never part of the real site.
      if (url.pathname === "/__demo/code") {
        res.end(JSON.stringify({ code: await world.lastCode() }));
        return;
      }
      if (url.pathname === "/__demo/advance") {
        await world.advance(Number(url.searchParams.get("s") || 0));
        res.end("{}");
        return;
      }
      const headers = {};
      for (const [k, v] of Object.entries(req.headers)) if (!k.startsWith(":")) headers[k] = Array.isArray(v) ? v.join(", ") : v;
      const hasBody = !["GET", "HEAD"].includes(req.method);
      const request = new Request("https://" + req.headers.host + req.url, { method: req.method, headers, body: hasBody ? body : undefined });
      const out = await world.call(request);
      res.statusCode = world.served(out) ? 200 : out.status;
      for (const [k, v] of out.headers.entries()) if (k !== "set-cookie" && !world.served(out)) res.setHeader(k, v);
      const cookies = out.headers.getSetCookie ? out.headers.getSetCookie() : [];
      if (cookies.length) res.setHeader("set-cookie", cookies);
      if (world.served(out)) {
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(STUB);
      } else {
        res.end(Buffer.from(await out.arrayBuffer()));
      }
    } catch (e) {
      res.statusCode = 500;
      res.end("demo server error: " + e.message);
    }
  });

server.listen(0, "127.0.0.1", () => {
  process.stdout.write(JSON.stringify({ port: server.address().port }) + "\n");
});
process.stdin.resume();
process.stdin.on("end", async () => {
  server.close();
  await world.stop();
  process.exit(0);
});
