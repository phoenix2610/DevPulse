"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { setToken } from "@/lib/api";

const LINKS = [["/", "Analyses"], ["/analytics", "Analytics"], ["/settings", "Settings"]] as const;

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-mono font-medium tracking-tight text-ink-100 ${className}`}>
      <svg viewBox="0 0 24 12" className="h-3 w-6 text-pulse-500" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
        <path d="M0 6h6l2-4 3 9 3-11 2 6h8" />
      </svg>
      devpulse
    </span>
  );
}

export default function Nav() {
  const path = usePathname();
  const router = useRouter();
  return (
    <header className="mb-8 flex items-center gap-6 border-b border-ink-800 pb-4">
      <Link href="/"><Wordmark /></Link>
      <nav className="flex gap-1 text-sm">
        {LINKS.map(([href, label]) => (
          <Link key={href} href={href} aria-current={path === href ? "page" : undefined}
            className={`rounded px-3 py-1.5 ${path === href ? "bg-ink-800 text-white" : "text-ink-400 hover:text-white"}`}>{label}</Link>
        ))}
      </nav>
      <button className="ml-auto text-sm text-ink-400 hover:text-white" onClick={() => { setToken(null); router.push("/login"); }}>Sign out</button>
    </header>
  );
}
