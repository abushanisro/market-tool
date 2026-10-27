"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type Entry = { title: string; section: string; href: string; method?: string };

export default function Header({ index }: { index: Entry[] }) {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen(true); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 10); else { setQ(""); setSel(0); } }, [open]);

  const results = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    const scored = index.map((e) => {
      const hay = `${e.title} ${e.section}`.toLowerCase();
      if (!terms.every((t) => hay.includes(t))) return null;
      return { e, s: (e.title.toLowerCase().startsWith(terms[0] ?? "") ? 0 : 1) + (e.method ? 0.5 : 0) };
    }).filter(Boolean) as { e: Entry; s: number }[];
    return scored.sort((a, b) => a.s - b.s).slice(0, 40).map((x) => x.e);
  }, [q, index]);

  const toggleTheme = () => {
    const cur = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = cur;
    try { localStorage.setItem("emithran-theme", cur); } catch { /* ignore */ }
  };
  const go = (href: string) => { setOpen(false); router.push(href); };

  return (
    <header className="hdr">
      <div className="hdr-top">
        <button className="icon-btn menu-toggle" aria-label="Open navigation" onClick={() => document.querySelector(".side")?.classList.toggle("open")}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M3 12h18M3 18h18" /></svg>
        </button>
        <Link href="/docs/general/introduction" className="logo">
          <img className="logo-mark" src="/logo.png" alt="" width={28} height={28} />Emithran<span className="sep">/</span><span className="sub">API Reference</span>
        </Link>
        <button className="search-btn" onClick={() => setOpen(true)} aria-label="Search documentation">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <span>Search documentation</span><kbd>⌘</kbd><kbd>K</kbd>
        </button>
        <div className="hdr-spacer" />
        <a className="hdr-link" href="https://www.emithran.com" target="_blank" rel="noreferrer">emithran.com ↗</a>
        <button className="icon-btn" onClick={toggleTheme} aria-label="Toggle dark mode" title="Toggle theme">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" /></svg>
        </button>
      </div>
      <nav className="subnav">
        <Link href="/docs/general/introduction" className={path.startsWith("/docs/general") ? "on" : ""}>Overview</Link>
        <Link href="/docs/ontologies/ontology-objects/basics" className={path.startsWith("/docs") && !path.startsWith("/docs/general") ? "on" : ""}>REST API</Link>
        <Link href="/console" className={path.startsWith("/console") ? "on" : ""}>Developer Console</Link>
        <span className="ver">Version 2.0</span>
      </nav>
      {open && (
        <div className="modal-back" onClick={() => setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Search">
            <input ref={input} value={q} placeholder="Search endpoints, guides, object types…"
              onChange={(e) => { setQ(e.target.value); setSel(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
                if (e.key === "Enter" && results[sel]) go(results[sel].href);
              }} />
            <ul>
              {results.map((r, i) => (
                <li key={r.href}><a href={r.href} className={i === sel ? "sel" : ""} onClick={(e) => { e.preventDefault(); go(r.href); }}>
                  {r.method && <span className={`method ${r.method}`} style={{ fontSize: 10, padding: "1px 5px" }}>{r.method}</span>}
                  <span>{r.title}</span><span className="sec">{r.section}</span></a></li>
              ))}
              {!results.length && <li className="note" style={{ padding: 12 }}>No results</li>}
            </ul>
          </div>
        </div>
      )}
    </header>
  );
}
