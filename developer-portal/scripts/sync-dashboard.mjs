// Copies the dashboard (static HTML/JS/CSS, single source: spider-platform/emithran/web) into public/dashboard so Vercel serves it behind sign-in.
// Runs before `next dev` and `next build`. The copy is generated and gitignored.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const src = resolve(root, "../spider-platform/emithran/web");
const dest = resolve(root, "public/dashboard");
if (!existsSync(src)) {
  console.error(`Dashboard source not found at ${src}. On Vercel, enable "Include source files outside of the Root Directory in the Build Step".`);
  process.exit(1);
}
rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
console.log(`dashboard synced: ${src} -> ${dest}`);
