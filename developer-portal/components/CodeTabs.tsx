"use client";
import { useState } from "react";

export default function CodeTabs({ tabs, title, status }: { tabs: { label: string; html: string; raw: string }[]; title?: string; status?: number }) {
  const [i, setI] = useState(0);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(tabs[i].raw); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };
  return (
    <div className="codebox">
      <div className="codebox-h">
        {title && <span className="title">{title}{status ? <span className={`status ${status >= 400 ? "e" : ""}`}>{status}</span> : null}</span>}
        {tabs.length > 1 && tabs.map((t, j) => <button key={t.label} className={j === i ? "on" : ""} onClick={() => setI(j)}>{t.label}</button>)}
        <span className="sp" />
        <button className="copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre><code dangerouslySetInnerHTML={{ __html: tabs[i].html }} /></pre>
    </div>
  );
}
