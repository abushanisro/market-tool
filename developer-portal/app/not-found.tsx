import Link from "next/link";
export default function NotFound() {
  return <div className="console"><h1>Page not found</h1><p className="lead">This page does not exist in API version 2.0.</p><Link className="btn primary" href="/docs/general/introduction">Back to the introduction</Link></div>;
}
