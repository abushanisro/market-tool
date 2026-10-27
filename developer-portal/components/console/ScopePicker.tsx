"use client";
export const SCOPES: [string, string][] = [
  ["api:ontologies-read", "Ontology metadata and objects"], ["api:datasets-read", "Datasets, rows and original files"],
  ["api:market-read", "Market & material intelligence"], ["api:orchestration-read", "Sources and builds"],
  ["api:orchestration-write", "Start / cancel builds"], ["api:admin", "Manage clients, keys, usage"],
];
export default function ScopePicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="scope-list">
      {SCOPES.map(([s, d]) => (
        <label key={s}><input type="checkbox" checked={value.includes(s)} onChange={(e) => onChange(e.target.checked ? [...value, s] : value.filter((x) => x !== s))} />
          <span><code>{s}</code> <span className="note">— {d}</span></span></label>
      ))}
    </div>
  );
}
