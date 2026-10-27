import fs from "node:fs";
import path from "node:path";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import CodeTabs from "@/components/CodeTabs";
import TryIt from "@/components/TryIt";
import { ExampleFields, ParamList, SchemaProps } from "@/components/SchemaTree";
import { highlight } from "@/lib/highlight";
import { renderMarkdown } from "@/lib/markdown";
import {
  allSlugs, codeSamples, errorCatalog, exampleFor, examplesGeneratedAt, fieldsFromExample, objectCounts, objectSamples, objectTypes,
  operations, resolvePage, resourceBlurb, slugify, type Operation, type ObjectTypeDoc,
} from "@/lib/spec";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "https://api.emithran.com";
type Toc = { id: string; text: string; depth: number }[];

export function generateStaticParams() {
  return allSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const p = resolvePage((await params).slug);
  if (!p) return {};
  const title = p.kind === "guide" ? p.title : p.kind === "operation" ? `${p.op.summary} (v2)` : p.kind === "resource" ? `${p.resource} basics` : p.type.displayName;
  return { title };
}

function TocNav({ items }: { items: Toc }) {
  if (!items.length) return <div />;
  return (
    <nav className="toc" aria-label="On this page"><h6>Contents</h6>
      {items.map((h) => <a key={h.id} href={`#${h.id}`} className={h.depth === 3 ? "l3" : ""}>{h.text}</a>)}</nav>
  );
}

function Crumbs({ parts }: { parts: string[] }) {
  return <div className="crumbs">{parts.map((p) => <span key={p}>{p}</span>)}</div>;
}

const json = (v: unknown) => JSON.stringify(v, null, 2);

/* ------------------------------------------------------------------ guide */
function Guide({ slug, title }: { slug: string; title: string }) {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "guides", `${slug}.md`), "utf8");
  const { html, headings } = renderMarkdown(md);
  let extra: React.ReactNode = null;
  if (slug === "errors") {
    headings.push({ id: "error-reference", text: "Error reference", depth: 2 });
    extra = (<><h2 id="error-reference">Error reference</h2>
      <table><thead><tr><th>errorName</th><th>errorCode</th><th>Description</th></tr></thead>
        <tbody>{errorCatalog.map((e) => <tr key={e.errorName}><td><code>{e.errorName}</code></td><td><code>{e.errorCode}</code></td><td>{e.description}</td></tr>)}</tbody></table></>);
  }
  if (slug === "introduction") {
    headings.push({ id: "at-a-glance", text: "At a glance", depth: 2 });
    extra = (<><h2 id="at-a-glance">At a glance</h2><div className="cards">
      {[["Object types", objectTypes.length], ["Operations", operations.length], ["Materials", objectCounts.Material], ["Datasets", objectCounts.Dataset],
        ["Machines", objectCounts.Machine], ["Observations", objectCounts.Observation]].map(([k, v]) =>
        <div className="card" key={String(k)}><div className="num">{Number(v ?? 0).toLocaleString("en-US")}</div><p>{k}</p></div>)}
    </div><p className="note">Counts from the database snapshot used to generate this reference ({examplesGeneratedAt.slice(0, 10)}).</p></>);
  }
  return (
    <div className="page"><article className="article"><Crumbs parts={["General", "Overview"]} /><h1>{title}</h1>
      <div dangerouslySetInnerHTML={{ __html: html }} />{extra}</article><TocNav items={headings} /></div>
  );
}

/* ------------------------------------------------------------------ resource basics */
function Resource({ group, resource, ops }: { group: string; resource: string; ops: Operation[] }) {
  const toc: Toc = [{ id: "operations", text: "Operations", depth: 2 }];
  const related = resource === "Ontology Objects" || resource === "Object Types";
  return (
    <div className="page"><article className="article"><Crumbs parts={[group, resource]} /><h1>{resource.replace(/s$/, "")} basics</h1>
      <p>{resourceBlurb(resource)}</p>
      <h2 id="operations">Operations</h2>
      <table><thead><tr><th>Operation</th><th>Endpoint</th><th>Scope</th></tr></thead><tbody>
        {ops.map((o) => <tr key={o.key}><td><Link href={`/docs/${o.slug.join("/")}`}>{o.summary}</Link>{o.preview && <span className="badge warn" style={{ marginLeft: 6 }}>Preview</span>}</td>
          <td><span className={`method ${o.method}`}>{o.method}</span> <code>{o.path.replace("/api/v2", "")}</code></td><td>{o.scopes.map((s) => <code key={s}>{s}</code>)}</td></tr>)}
      </tbody></table>
      {related && (<><h2 id="object-types">Object types</h2><div className="cards">
        {objectTypes.map((t) => <Link className="card" key={t.apiName} href={`/docs/ontologies/object-types-reference/${slugify(t.apiName)}`}>
          <h3>{t.displayName}</h3><p><code>{t.apiName}</code> · {Number(objectCounts[t.apiName] ?? 0).toLocaleString("en-US")} objects</p></Link>)}</div></>)}
    </article><TocNav items={related ? [...toc, { id: "object-types", text: "Object types", depth: 2 }] : toc} /></div>
  );
}

/* ------------------------------------------------------------------ object type */
function ObjectType({ type }: { type: ObjectTypeDoc }) {
  const sample = objectSamples[type.apiName]?.[0];
  const toc: Toc = [{ id: "properties", text: "Properties", depth: 2 }, { id: "links", text: "Links", depth: 2 }, { id: "example-object", text: "Example object", depth: 2 }, { id: "query-it", text: "Query it", depth: 2 }];
  const searchBody = { where: { type: "isNull", field: Object.keys(type.properties).find((k) => type.properties[k].filterable && k !== type.primaryKey) ?? type.primaryKey, value: false }, pageSize: 10 };
  const curl = `curl -X POST "${BASE}/api/v2/ontologies/manufacturing/objects/${type.apiName}/search" \\\n  -H "Authorization: Bearer $EMITHRAN_TOKEN" -H "Content-Type: application/json" \\\n  -d '${JSON.stringify(searchBody)}'`;
  return (
    <div className="page"><article className="article"><Crumbs parts={["Ontologies", "Object type reference"]} /><h1>{type.displayName}</h1>
      <div className="badges"><span className="badge brand">apiName: {type.apiName}</span><span className="badge">primary key: {type.primaryKey}</span>
        <span className="badge">title: {type.titleProperty}</span><span className="badge">{Number(objectCounts[type.apiName] ?? 0).toLocaleString("en-US")} objects</span></div>
      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(type.description).html }} />
      <h2 id="properties">Properties</h2>
      <table><thead><tr><th>Property</th><th>Type</th><th>Filterable</th><th>Description</th></tr></thead><tbody>
        {Object.entries(type.properties).map(([k, p]) => <tr key={k}><td><code>{k}</code>{k === type.primaryKey && <span className="badge brand" style={{ marginLeft: 6 }}>PK</span>}</td>
          <td><code>{p.dataType.type}</code></td><td>{p.filterable ? "yes" : "no"}</td><td dangerouslySetInnerHTML={{ __html: renderMarkdown(p.description).html }} /></tr>)}
      </tbody></table>
      <h2 id="links">Links</h2>
      {type.links.length ? <table><thead><tr><th>Link</th><th>Target</th><th>Cardinality</th><th>Description</th></tr></thead><tbody>
        {type.links.map((l) => <tr key={l.apiName}><td><code>{l.apiName}</code></td><td><Link href={`/docs/ontologies/object-types-reference/${slugify(l.objectTypeApiName)}`}>{l.objectTypeApiName}</Link></td><td>{l.cardinality}</td><td>{l.description}</td></tr>)}
      </tbody></table> : <p>No outgoing links.</p>}
      <h2 id="example-object">Example object</h2>
      <p className="note">First object returned by List Objects on the reference database.</p>
      {sample ? <CodeTabs title="JSON" tabs={[{ label: "JSON", html: highlight(json(sample), "json"), raw: json(sample) }]} /> : <p>No objects.</p>}
      <h2 id="query-it">Query it</h2>
      <CodeTabs tabs={[{ label: "cURL", html: highlight(curl, "bash"), raw: curl }]} />
      <p>See <Link href="/docs/ontologies/ontology-objects/search-objects">Search Objects</Link> and <Link href="/docs/general/filtering">Search &amp; aggregation</Link>.</p>
    </article><TocNav items={toc} /></div>
  );
}

/* ------------------------------------------------------------------ operation */
function OperationPage({ op }: { op: Operation }) {
  const ex = exampleFor(op);
  const pathP = op.params.filter((p) => p.in === "path"), queryP = op.params.filter((p) => p.in === "query");
  const toc: Toc = [];
  if (pathP.length) toc.push({ id: "path-parameters", text: "Path parameters", depth: 2 });
  if (queryP.length) toc.push({ id: "query-parameters", text: "Query parameters", depth: 2 });
  if (op.body) toc.push({ id: "request-body", text: "Request body", depth: 2 });
  toc.push({ id: "response-body", text: "Response body", depth: 2 }, { id: "errors", text: "Errors", depth: 2 }, { id: "examples", text: "Examples", depth: 2 },
    { id: "request", text: "Request", depth: 3 }, { id: "response", text: "Response", depth: 3 }, { id: "try-it", text: "Try it", depth: 2 });
  const samples = codeSamples(op, BASE);
  const respFields = ex ? fieldsFromExample(ex.response) : [];
  const names = new Set<string>(["MissingCredentials", "InvalidToken", ...(op.scopes.length ? ["MissingScope"] : []), "RateLimitExceeded"]);
  if (op.params.some((p) => p.name === "pageToken") || op.body?.schema?.properties?.pageToken) ["InvalidPageSize", "InvalidPageToken"].forEach((n) => names.add(n));
  if (op.path.includes("{ontology}")) ["OntologyNotFound", "ObjectTypeNotFound"].forEach((n) => names.add(n));
  if (op.path.includes("{primaryKey}")) names.add("ObjectNotFound");
  if (op.path.includes("/search") || op.path.includes("/aggregate")) ["InvalidQuery", "PropertiesNotFound", "PropertyNotFilterable"].forEach((n) => names.add(n));
  if (op.path.includes("datasetRid")) ["DatasetNotFound", "InvalidRid"].forEach((n) => names.add(n));
  if (op.path.includes("/links/")) names.add("LinkTypeNotFound");
  if (op.preview) names.add("ApiFeaturePreviewUsageOnly");
  if (op.path.includes("materialRid") || op.path.includes("compare")) names.add("MaterialNotFound");
  if (op.path.includes("/builds")) ["BuildNotFound", "BuildNotPermitted"].forEach((n) => names.add(n));
  if (op.path.includes("oauth2")) { ["MissingCredentials", "InvalidToken", "RateLimitExceeded"].forEach((n) => names.delete(n)); ["InvalidClientCredentials", "InvalidScope", "UnsupportedGrantType"].forEach((n) => names.add(n)); }
  if (!op.scopes.length && !op.path.includes("oauth2")) { names.clear(); }
  names.add("InvalidArgument");
  const errs = errorCatalog.filter((e) => names.has(e.errorName));
  const isForm = op.body?.contentType.includes("form");
  return (
    <div className="page"><article className="article"><Crumbs parts={[op.group, op.resource]} />
      <h1>{op.summary}</h1>
      <div className="endpoint"><span className={`method ${op.method}`}>{op.method}</span><code>{op.path}</code></div>
      <div className="badges">
        {op.scopes.length ? op.scopes.map((s) => <span key={s} className="badge brand">Scope: {s}</span>) : <span className="badge">No authentication required</span>}
        {op.preview && <span className="badge warn">Preview · requires preview=true</span>}
        <span className="badge">Version 2.0</span>
      </div>
      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(op.description).html }} />
      {pathP.length > 0 && (<><h2 id="path-parameters">Path parameters</h2><ParamList params={pathP} /></>)}
      {queryP.length > 0 && (<><h2 id="query-parameters">Query parameters</h2><ParamList params={queryP} /></>)}
      {op.body && (<><h2 id="request-body">Request body</h2><p className="note">Content type <code>{op.body.contentType}</code></p><SchemaProps schema={op.body.schema} /></>)}
      <h2 id="response-body">Response body</h2>
      {op.responseSchema ? <SchemaProps schema={op.responseSchema} /> : respFields.length ? (<><p className="note">Fields present in the example response below.</p><ExampleFields fields={respFields.slice(0, 60)} /></>)
        : op.path.endsWith("/files/content") || (op.path.endsWith("/readTable")) ? <p>Binary file / CSV stream when requested; JSON otherwise.</p> : <p>See example.</p>}
      <h2 id="errors">Errors</h2>
      <p>All errors use the <Link href="/docs/general/errors">standard error envelope</Link>.</p>
      <table><thead><tr><th>errorName</th><th>errorCode</th><th>When</th></tr></thead><tbody>
        {errs.map((e) => <tr key={e.errorName}><td><code>{e.errorName}</code></td><td><code>{e.errorCode}</code></td><td>{e.description}</td></tr>)}</tbody></table>
      <h2 id="examples">Examples</h2>
      <h3 id="request">Request</h3>
      <CodeTabs tabs={[{ label: "cURL", html: highlight(samples.curl, "bash"), raw: samples.curl }, { label: "Python", html: highlight(samples.python, "python"), raw: samples.python },
        { label: "TypeScript", html: highlight(samples.typescript, "ts"), raw: samples.typescript }]} />
      <h3 id="response">Response</h3>
      {ex ? (<><CodeTabs title="Response" status={ex.status} tabs={[{ label: "JSON", html: highlight(json(ex.response), "json"), raw: json(ex.response) }]} />
        <p className="note">Captured from the reference database on {examplesGeneratedAt.slice(0, 10)}; long arrays trimmed to three items.</p></>)
        : <p className="note">This operation changes state or requires admin credentials, so no live example was captured. Use Try it below.</p>}
      <TryIt method={op.method} path={op.path} params={op.params} body={ex?.request?.body ?? op.body?.example} examplePath={ex?.request?.path}
        exampleQuery={ex?.request?.query ?? undefined} scopes={op.scopes} isForm={!!isForm} />
    </article><TocNav items={toc} /></div>
  );
}

export default async function DocPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const p = resolvePage((await params).slug);
  if (!p) notFound();
  if (p.kind === "guide") return <Guide slug={p.slug} title={p.title} />;
  if (p.kind === "resource") return <Resource group={p.group} resource={p.resource} ops={p.ops} />;
  if (p.kind === "objectType") return <ObjectType type={p.type} />;
  return <OperationPage op={p.op} />;
}

export const dynamicParams = false;
