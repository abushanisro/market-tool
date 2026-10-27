import { resolve, typeLabel } from "@/lib/spec";
import { renderMarkdown } from "@/lib/markdown";

type S = { [k: string]: any };

function Desc({ text }: { text?: string }) {
  if (!text) return null;
  return <div className="d" dangerouslySetInnerHTML={{ __html: renderMarkdown(text).html }} />;
}

export function SchemaProps({ schema, depth = 0 }: { schema: S; depth?: number }) {
  const s = resolve(schema);
  const props = s.properties ?? {};
  const req: string[] = s.required ?? [];
  return (
    <div className="params">
      {Object.entries<S>(props).map(([name, p]) => {
        const r = resolve(p);
        const child = r.type === "object" && r.properties ? r : r.type === "array" && resolve(r.items ?? {}).properties ? resolve(r.items) : null;
        return (
          <div className="param" key={name}>
            <div className="param-h"><span className="n">{name}</span><span className="t">{typeLabel(p)}</span>
              {req.includes(name) ? <span className="req">required</span> : <span className="opt">optional</span>}
              {r.default !== undefined && r.default !== null ? <span className="opt">default {JSON.stringify(r.default)}</span> : null}</div>
            <Desc text={r.description ?? p.description} />
            {child && depth < 4 && <details><summary>Show child attributes</summary><SchemaProps schema={child} depth={depth + 1} /></details>}
          </div>
        );
      })}
    </div>
  );
}

export function ParamList({ params }: { params: { name: string; type: string; required: boolean; description?: string; default?: any }[] }) {
  return (
    <div className="params">
      {params.map((p) => (
        <div className="param" key={p.name}>
          <div className="param-h"><span className="n">{p.name}</span><span className="t">{p.type}</span>
            {p.required ? <span className="req">required</span> : <span className="opt">optional</span>}
            {p.default !== undefined && p.default !== null ? <span className="opt">default {JSON.stringify(p.default)}</span> : null}</div>
          <Desc text={p.description} />
        </div>
      ))}
    </div>
  );
}

export function ExampleFields({ fields }: { fields: { path: string; type: string; sample?: string }[] }) {
  return (
    <div className="params">
      {fields.map((f) => (
        <div className="param" key={f.path}>
          <div className="param-h"><span className="n">{f.path}</span><span className="t">{f.type}</span></div>
          {f.sample !== undefined && <div className="d note">e.g. <code>{f.sample}</code></div>}
        </div>
      ))}
    </div>
  );
}
