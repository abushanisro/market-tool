"use client";
import { useRouter } from "next/navigation";
export default function SignOut() {
  const router = useRouter();
  return <button className="btn sm" onClick={async () => { await fetch("/api/console-auth", { method: "DELETE" }); router.refresh(); }}>Sign out</button>;
}
