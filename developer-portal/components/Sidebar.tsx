"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import type { NavSection } from "@/lib/spec";

export default function Sidebar({ nav }: { nav: NavSection[] }) {
  const path = usePathname();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.classList.remove("open");
    const el = ref.current?.querySelector("a.on") as HTMLElement | null;
    const box = ref.current;
    if (el && box) {
      const r = el.getBoundingClientRect(), c = box.getBoundingClientRect();
      if (r.top < c.top || r.bottom > c.bottom) box.scrollTop += r.top - c.top - box.clientHeight / 2;  // never scrolls the window
    }
  }, [path]);
  return (
    <aside className="side" ref={ref} aria-label="API reference navigation">
      {nav.map((s) => (
        <div className="grp" key={s.title}>
          <h4>{s.title}</h4>
          {s.resources.map((r) => (
            <div key={r.title}>
              <h5>{r.title}</h5>
              {r.items.map((i) => (
                <Link key={i.href} href={i.href} className={path === i.href ? "on" : ""}>
                  {i.method ? <span className="m">{i.method}</span> : null}
                  <span>{i.title}{i.preview ? " ·β" : ""}</span>
                </Link>
              ))}
            </div>
          ))}
        </div>
      ))}
    </aside>
  );
}
