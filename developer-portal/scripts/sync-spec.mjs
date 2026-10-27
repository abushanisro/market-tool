// Refresh content/openapi.json and content/errors.json from a running API.
// For object-type samples and real example responses run, in spider-platform:
//   EMITHRAN_ADMIN_TOKEN=... python scripts/export_api_docs.py ../developer-portal/content
import { writeFile } from "node:fs/promises";
const base = (process.env.EMITHRAN_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
for (const [path, file] of [["/api/v2/openapi.json", "openapi.json"], ["/api/v2/meta/errors", "errors.json"]]) {
  const r = await fetch(base + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  await writeFile(new URL(`../content/${file}`, import.meta.url), JSON.stringify(await r.json(), null, 1));
  console.log("updated", file);
}
