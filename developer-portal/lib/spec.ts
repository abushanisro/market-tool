/**
 * Builds the documentation model from the exported OpenAPI document, the object-type catalogue and
 * real example responses (content/*.json, produced by spider-platform/scripts/export_api_docs.py).
 */
import openapi from "@/content/openapi.json";
import examplesFile from "@/content/examples.json";
import objectTypesFile from "@/content/object-types.json";
import errorsFile from "@/content/errors.json";

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Schema = { [k: string]: any };

export interface Param { name: string; in: string; required: boolean; type: string; description?: string; example?: Json; default?: Json }
export interface Operation {
  key: string; method: string; path: string; summary: string; description: string; group: string; resource: string;
  scopes: string[]; preview: boolean; params: Param[]; body?: { contentType: string; schema: Schema; example?: Json };
  responseSchema?: Schema; slug: string[];
}
export interface ObjectTypeDoc {
  apiName: string; displayName: string; pluralDisplayName: string; description: string; primaryKey: string; titleProperty: string;
  rid: string; properties: Record<string, { dataType: { type: string }; description: string; filterable: boolean }>;
  links: { apiName: string; objectTypeApiName: string; cardinality: string; description: string }[];
}
export interface NavItem { title: string; href: string; method?: string; preview?: boolean }
export interface NavSection { title: string; resources: { title: string; items: NavItem[] }[] }

const spec = openapi as any;
export const examples = (examplesFile as any).examples as Record<string, any>;
export const examplesGeneratedAt = (examplesFile as any).generatedAt as string;
export const objectTypes = (objectTypesFile as any).objectTypes as ObjectTypeDoc[];
export const objectCounts = (objectTypesFile as any).counts as Record<string, number>;
export const objectSamples = (objectTypesFile as any).samples as Record<string, any[]>;
export const errorCatalog = (errorsFile as any).data as { errorName: string; errorCode: string; description: string }[];
export const securitySchemes = spec.components?.securitySchemes ?? {};

export const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export function resolve(s: Schema | undefined, depth = 0): Schema {
  if (!s || depth > 8) return s ?? {};
  if (s.$ref) {
    const name = s.$ref.split("/").pop();
    return resolve({ ...spec.components.schemas[name], ...Object.fromEntries(Object.entries(s).filter(([k]) => k !== "$ref")) }, depth + 1);
  }
  if (s.anyOf) {
    const nonNull = s.anyOf.filter((x: Schema) => x.type !== "null");
    if (nonNull.length === 1) return { ...resolve(nonNull[0], depth + 1), nullable: true, description: s.description ?? nonNull[0].description, default: s.default, title: s.title };
  }
  return s;
}

export function typeLabel(s: Schema): string {
  const r = resolve(s);
  if (r.type === "array") return `list<${typeLabel(r.items ?? {})}>`;
  if (r.type === "object" && r.additionalProperties && typeof r.additionalProperties === "object") return `map<string, ${typeLabel(r.additionalProperties)}>`;
  if (r.enum) return r.enum.map((e: any) => JSON.stringify(e)).join(" | ");
  if (r.anyOf) return r.anyOf.map(typeLabel).join(" | ");
  if (r.format === "date") return "date";
  return r.type ?? (r.properties ? "object" : "any");
}

const RESOURCE_BLURBS: Record<string, string> = {
  Authentication: "OAuth 2.0 client-credentials tokens and API keys. Every endpoint except token issuance and the meta endpoints requires a bearer token.",
  Errors: "Machine-readable catalogue of error names returned by the API.",
  Ontologies: "The `manufacturing` ontology models everything imported from the memory/ folder — categories, datasets, materials, machines, processes, observations — plus crawled sources, documents and standards.",
  "Object Types": "An object type describes one kind of object: its properties, their data types, the primary key and outgoing links.",
  "Ontology Objects": "Read, search and aggregate objects of any type with a single, uniform set of endpoints.",
  "Linked Objects": "Traverse relationships such as category → materials or material → price observations.",
  Datasets: "Every imported file (CSV, JSON, XLSX sheet, documents) is a dataset with its original column names and rows preserved verbatim.",
  Files: "Download the original file byte-for-byte; verify it with the SHA-256 checksum.",
  "Exchange Rates": "Crawled FX rates against USD and the drift versus the internal exchange_rate_table.csv.",
  Prices: "Dated price observations and regional USD/kg benchmarks, with provenance and quality status.",
  "Labor & Overhead": "Average labour and overhead rates per country derived from machine / work-center records.",
  Indicators: "Macro and industry indicators such as manufacturing value added.",
  Materials: "Grade comparison and process-compatible alternatives that never estimate missing data.",
  Processes: "Material families available per manufacturing process.",
  Sources: "The registry of external sources with licence, verification status and freshness.",
  Builds: "Crawl jobs. A build fetches one source within its configured scope and stores normalized records.",
  Clients: "OAuth 2.0 clients for server-to-server integrations.",
  "API Keys": "Long-lived bearer keys for scripts and notebooks.",
  Usage: "Request volume, error rate and latency per day, endpoint and principal.",
};
export const resourceBlurb = (r: string) => RESOURCE_BLURBS[r] ?? "";

function collectOperations(): Operation[] {
  const ops: Operation[] = [];
  for (const [path, methods] of Object.entries<any>(spec.paths)) {
    for (const [method, op] of Object.entries<any>(methods)) {
      const params: Param[] = (op.parameters ?? []).map((p: any) => {
        const sc = resolve(p.schema);
        return { name: p.name, in: p.in, required: !!p.required, type: typeLabel(p.schema ?? {}), description: p.description ?? sc.description,
                 example: p.examples ? (Object.values<any>(p.examples)[0]?.value ?? p.examples[0]) : undefined, default: sc.default };
      });
      let body: Operation["body"];
      const content = op.requestBody?.content;
      if (content) {
        const ct = Object.keys(content)[0];
        const ex = content[ct].examples ? (Object.values<any>(content[ct].examples)[0] as any).value : undefined;
        body = { contentType: ct, schema: resolve(content[ct].schema), example: ex };
      }
      const resp = op.responses?.["200"]?.content?.["application/json"]?.schema;
      const respSchema = resp && (resp.$ref || resp.properties) ? resolve(resp) : undefined;
      const group = op["x-group"] ?? "General", resource = op["x-resource"] ?? "Misc";
      ops.push({ key: `${method.toUpperCase()} ${path}`, method: method.toUpperCase(), path, summary: op.summary, description: op.description ?? "",
                 group, resource, scopes: op["x-scopes"] ?? [], preview: !!op["x-preview"], params, body, responseSchema: respSchema,
                 slug: [slugify(group), slugify(resource), slugify(op.summary)] });
    }
  }
  return ops;
}
export const operations = collectOperations();
export const groupOrder: string[] = spec["x-group-order"] ?? [];

export const GUIDES: { slug: string; title: string }[] = [
  { slug: "introduction", title: "Introduction" }, { slug: "getting-started", title: "Getting started" },
  { slug: "authentication", title: "Authentication" }, { slug: "data-model", title: "Data model (memory/)" },
  { slug: "resource-identifiers", title: "Resource identifiers" }, { slug: "filtering", title: "Search & aggregation" },
  { slug: "paging", title: "Paging" }, { slug: "versioning", title: "Versioning" }, { slug: "limits", title: "Limits" },
  { slug: "errors", title: "Errors" },
];

export function buildNav(): NavSection[] {
  const nav: NavSection[] = [{ title: "General", resources: [{ title: "Overview", items: GUIDES.map((g) => ({ title: g.title, href: `/docs/general/${g.slug}` })) }] }];
  const groups = [...new Set([...groupOrder, ...operations.map((o) => o.group)])];
  for (const g of groups) {
    const ops = operations.filter((o) => o.group === g);
    if (!ops.length) continue;
    let section = nav.find((n) => n.title === g);
    if (!section) { section = { title: g, resources: [] }; nav.push(section); }
    const resources = [...new Set(ops.map((o) => o.resource))];
    for (const r of resources) {
      const items: NavItem[] = [{ title: `${r.replace(/s$/, "").replace(/ie$/, "y")} basics`, href: `/docs/${slugify(g)}/${slugify(r)}/basics` }];
      ops.filter((o) => o.resource === r).forEach((o) => items.push({ title: o.summary, href: `/docs/${o.slug.join("/")}`, method: o.method, preview: o.preview }));
      section.resources.push({ title: r, items });
    }
    if (g === "Ontologies") {
      section.resources.push({ title: "Object type reference", items: objectTypes.map((t) => ({ title: t.displayName, href: `/docs/ontologies/object-types-reference/${slugify(t.apiName)}` })) });
    }
  }
  return nav;
}

export type Resolved =
  | { kind: "guide"; slug: string; title: string }
  | { kind: "resource"; group: string; resource: string; ops: Operation[] }
  | { kind: "operation"; op: Operation }
  | { kind: "objectType"; type: ObjectTypeDoc };

export function resolvePage(slug: string[]): Resolved | null {
  if (slug[0] === "general" && slug.length === 2) {
    const g = GUIDES.find((x) => x.slug === slug[1]);
    return g ? { kind: "guide", slug: g.slug, title: g.title } : null;
  }
  if (slug[0] === "ontologies" && slug[1] === "object-types-reference" && slug[2]) {
    const t = objectTypes.find((x) => slugify(x.apiName) === slug[2]);
    return t ? { kind: "objectType", type: t } : null;
  }
  if (slug.length === 3 && slug[2] === "basics") {
    const ops = operations.filter((o) => slugify(o.group) === slug[0] && slugify(o.resource) === slug[1]);
    return ops.length ? { kind: "resource", group: ops[0].group, resource: ops[0].resource, ops } : null;
  }
  const op = operations.find((o) => o.slug.join("/") === slug.join("/"));
  return op ? { kind: "operation", op } : null;
}

export function allSlugs(): string[][] {
  const out: string[][] = GUIDES.map((g) => ["general", g.slug]);
  for (const o of operations) out.push(o.slug);
  for (const r of new Set(operations.map((o) => `${slugify(o.group)}/${slugify(o.resource)}`))) out.push([...r.split("/"), "basics"]);
  for (const t of objectTypes) out.push(["ontologies", "object-types-reference", slugify(t.apiName)]);
  return out;
}

export function searchIndex() {
  return buildNav().flatMap((s) => s.resources.flatMap((r) => r.items.map((i) => ({ title: i.title, section: `${s.title} · ${r.title}`, href: i.href, method: i.method }))));
}

export function exampleFor(op: Operation) {
  return examples[op.key];
}

/* ---------- code samples ---------- */
export function codeSamples(op: Operation, base: string) {
  const ex = exampleFor(op)?.request;
  const path = ex?.path ?? op.path;
  const q = ex?.query ? new URLSearchParams(Object.entries(ex.query).flatMap(([k, v]) => (Array.isArray(v) ? v.map((x) => [k, String(x)]) : [[k, String(v)]])) as [string, string][]).toString() : "";
  const url = `${base}${path}${q ? "?" + q : ""}`;
  const body = ex?.body ?? op.body?.example;
  const isForm = op.body?.contentType?.includes("x-www-form-urlencoded");
  const form = ex?.form as Record<string, string> | undefined;
  const auth = op.scopes.length ? true : false;
  const bodyJson = body ? JSON.stringify(body, null, 2) : "";
  let curl = `curl -X ${op.method} "${url}"`;
  if (auth) curl += ` \\\n  -H "Authorization: Bearer $EMITHRAN_TOKEN"`;
  if (isForm && form) curl += ` \\\n  -H "Content-Type: application/x-www-form-urlencoded"` + Object.entries(form).map(([k, v]) => ` \\\n  --data-urlencode "${k}=${k === "client_id" ? "$CLIENT_ID" : k === "client_secret" ? "$CLIENT_SECRET" : v}"`).join("");
  else if (body) curl += ` \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(body)}'`;
  const py = [
    "import os, requests", "",
    isForm ? `resp = requests.post(\n    "${base}${path}",\n    data={"grant_type": "client_credentials", "client_id": os.environ["CLIENT_ID"],\n          "client_secret": os.environ["CLIENT_SECRET"], "scope": "${form?.scope ?? ""}"},\n)`
      : `resp = requests.${op.method.toLowerCase()}(\n    "${base}${path}",\n` + (ex?.query ? `    params=${pyLiteral(ex.query)},\n` : "") +
        (auth ? `    headers={"Authorization": f"Bearer {os.environ['EMITHRAN_TOKEN']}"},\n` : "") + (body ? `    json=${pyLiteral(body)},\n` : "") + ")",
    "resp.raise_for_status()", "print(resp.json())",
  ].join("\n");
  const ts = isForm
    ? `const res = await fetch("${base}${path}", {\n  method: "POST",\n  headers: { "Content-Type": "application/x-www-form-urlencoded" },\n  body: new URLSearchParams({\n    grant_type: "client_credentials",\n    client_id: process.env.CLIENT_ID!,\n    client_secret: process.env.CLIENT_SECRET!,\n  }),\n});\nconst { access_token } = await res.json();`
    : `const res = await fetch("${url}", {\n  method: "${op.method}",\n  headers: {\n` + (auth ? `    Authorization: \`Bearer \${process.env.EMITHRAN_TOKEN}\`,\n` : "") +
      (body ? `    "Content-Type": "application/json",\n` : "") + `  },\n` + (body ? `  body: JSON.stringify(${bodyJson.replace(/\n/g, "\n  ")}),\n` : "") +
      `});\nif (!res.ok) throw new Error((await res.json()).errorName);\nconst data = await res.json();`;
  return { curl, python: py, typescript: ts };
}

function pyLiteral(v: any, indent = 4): string {
  const s = JSON.stringify(v, null, 4).replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None");
  return s.split("\n").map((l, i) => (i ? " ".repeat(indent) + l : l)).join("\n");
}

/** Field list inferred from a real example response (used when the endpoint has no declared response model). */
export function fieldsFromExample(v: any, prefix = "", out: { path: string; type: string; sample?: string }[] = [], depth = 0) {
  if (depth > 3 || v === null || v === undefined) return out;
  if (Array.isArray(v)) { if (v.length && typeof v[0] === "object") fieldsFromExample(v[0], prefix + "[]", out, depth + 1); return out; }
  if (typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      const t = x === null ? "null" : Array.isArray(x) ? `list<${x.length ? (typeof x[0] === "object" ? "object" : typeof x[0]) : "any"}>` : typeof x === "object" ? "object" : typeof x;
      out.push({ path: prefix ? `${prefix}.${k}` : k, type: t, sample: typeof x === "object" ? undefined : String(x).slice(0, 60) });
      if (typeof x === "object" && x !== null && depth < 2) fieldsFromExample(x, prefix ? `${prefix}.${k}` : k, out, depth + 1);
    }
  }
  return out;
}
