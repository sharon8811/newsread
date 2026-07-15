import Link from "next/link";

// Root 404: catches notFound() throws and any URL no route matches.
export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="mono-label">404 — not found</p>
      <h1 className="font-serif-nr text-display font-medium">
        This page doesn&apos;t exist.
      </h1>
      <Link href="/" className="btn mt-2">
        Back to your inbox
      </Link>
    </div>
  );
}
