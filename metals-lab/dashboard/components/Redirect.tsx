"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Old addresses keep working: bookmarks and the desktop icon land on the page that now holds the content. */
export function Redirect({ to, what }: { to: string; what: string }) {
  const router = useRouter();
  useEffect(() => {
    const q = window.location.search.slice(1);
    router.replace(q ? `${to}${to.includes("?") ? "&" : "?"}${q}` : to);
  }, [router, to]);
  return <p className="empty">{what} has moved. <Link href={to}>Go there now</Link>.</p>;
}
