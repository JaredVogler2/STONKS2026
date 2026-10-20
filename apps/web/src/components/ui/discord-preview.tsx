/* Read-only rendering of a stored Discord message payload (what the bot actually posted to a shared channel).
 * Buttons are shown inert: they only work inside Discord, where the bot verifies their signed ids. */

type Json = Record<string, unknown>;
interface Embed { title?: string; description?: string; color?: number; fields?: { name: string; value: string; inline?: boolean }[]; footer?: { text?: string } }

function Md({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>))}</>;
}

export function DiscordPreview({ message }: { message: Json }) {
  const embeds = (message.embeds as Embed[] | undefined) ?? [];
  const rows = ((message.components as { components?: { label?: string; style?: number }[] }[] | undefined) ?? []).flatMap((r) => r.components ?? []);
  return (
    <div className="rounded-lg bg-[#313338] p-3 text-[13px] leading-snug text-[#dbdee1]">
      {message.content ? <p className="mb-2 whitespace-pre-wrap"><Md text={String(message.content)} /></p> : null}
      {embeds.map((e, i) => (
        <div key={i} className="mb-2 rounded border-l-4 bg-[#2b2d31] px-3 py-2" style={{ borderColor: e.color !== undefined ? `#${e.color.toString(16).padStart(6, "0")}` : "#1e1f22" }}>
          {e.title ? <div className="mb-1 font-semibold text-white">{e.title}</div> : null}
          {e.description ? <p className="mb-2 whitespace-pre-wrap"><Md text={e.description} /></p> : null}
          <div className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-3">
            {(e.fields ?? []).map((f, j) => (
              <div key={j} className={f.inline ? "" : "sm:col-span-3"}>
                <div className="text-[12px] font-semibold text-white">{f.name}</div>
                <div className="whitespace-pre-wrap text-[12px]"><Md text={f.value} /></div>
              </div>
            ))}
          </div>
          {e.footer?.text ? <div className="mt-2 text-[11px] text-[#949ba4]">{e.footer.text}</div> : null}
        </div>
      ))}
      {rows.length ? (
        <div className="flex flex-wrap gap-1.5" aria-label="Discord buttons (work only in Discord)">
          {rows.map((b, i) => (
            <span key={i} className={`rounded px-2.5 py-1 text-[12px] font-medium text-white ${b.style === 1 ? "bg-[#5865f2]" : b.style === 4 ? "bg-[#da373c]" : "bg-[#4e5058]"}`}>{b.label}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
