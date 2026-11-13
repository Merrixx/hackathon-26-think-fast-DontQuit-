"use strict";
// Calls the locally running app's decision API. Node's http/https modules only,
// so the add-on has no npm dependencies.

const http = require("http");
const https = require("https");

function postJson(url, body, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    let target;
    try {
      target = new URL(url);
    } catch {
      reject(new Error(`invalid endpoint URL: ${url}`));
      return;
    }
    const data = Buffer.from(JSON.stringify(body));
    const lib = target.protocol === "https:" ? https : http;
    const req = lib.request(target, {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": data.length },
      timeout: timeoutMs,
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`app answered HTTP ${res.statusCode}: ${text.slice(0, 200)}`));
          return;
        }
        try {
          resolve(JSON.parse(text));
        } catch {
          reject(new Error("app answered with invalid JSON"));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error(`no answer within ${timeoutMs} ms`)));
    req.on("error", reject);
    req.end(data);
  });
}

module.exports = { postJson };
