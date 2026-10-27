import Sidebar from "@/components/Sidebar";
import { buildNav } from "@/lib/spec";

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="docs">
      <Sidebar nav={buildNav()} />
      <div>{children}
        <footer className="foot"><span>© {new Date().getFullYear()} Emithran</span><a href="/api/spec">OpenAPI 3.1 (JSON)</a><a href="/console">Developer Console</a><a href="https://www.emithran.com" target="_blank" rel="noreferrer">emithran.com</a></footer>
      </div>
    </div>
  );
}
