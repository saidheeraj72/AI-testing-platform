import { useEffect, useRef } from "react";
import type { Activity } from "../state/liveSession";

const ICON: Record<Activity["kind"], string> = {
  step: "▸", thought: "·", action: "", look: "⌕", replan: "↻", bug: "!", confirm: "?", info: "i", error: "✗",
};

export function ActivityFeed({ items }: { items: Activity[] }) {
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // Braces matter: Chrome's scrollIntoView returns a Promise, which React must not receive as a cleanup.
    end.current?.scrollIntoView({ block: "nearest" });
  }, [items.length]);
  if (items.length === 0) return <p className="empty">Waiting for the agent…</p>;
  return (
    <ul className="feed" aria-live="polite">
      {items.map((a) => {
        const icon = a.kind === "action" ? (a.ok ? "✓" : "✗") : ICON[a.kind];
        const cls = a.kind === "action" ? (a.ok ? "icon-ok" : "icon-bad") : a.kind === "bug" || a.kind === "error" ? "icon-bad" : "icon-muted";
        return (
          <li key={a.seq} className={a.kind}>
            <span className={cls}>{icon}</span>
            <span>
              {a.text}
              {a.detail && <span className="detail">{a.detail}</span>}
            </span>
          </li>
        );
      })}
      <li ref={end} aria-hidden="true" />
    </ul>
  );
}
