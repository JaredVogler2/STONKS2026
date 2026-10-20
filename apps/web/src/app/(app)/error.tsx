"use client";

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="mx-auto max-w-xl space-y-3 rounded-xl border border-neg/40 bg-neg/10 p-5">
      <h1 className="text-base font-semibold text-neg">This view could not load</h1>
      <p className="text-[13px] text-fg/80">
        The server could not read its data. Most often the database is unreachable (Supabase paused or DATABASE_URL wrong) or a migration is pending.
      </p>
      <ul className="list-disc space-y-1 pl-5 text-[13px] text-fg/80">
        <li>Check Operations → Health once the page recovers, or run <code>pnpm --filter @apex/web db:migrate</code>.</li>
        <li>If Supabase paused for inactivity, resume the project in the Supabase dashboard; the Pi keeps its spool and catches up.</li>
      </ul>
      {error.digest ? <p className="text-[11px] text-faint">Reference: {error.digest}</p> : null}
      <button type="button" onClick={reset} className="inline-flex min-h-9 items-center rounded-lg border border-line-strong bg-surface-2 px-3 text-[13px]">
        Try again
      </button>
    </div>
  );
}
