import { useFileUrl } from "../api/hooks";

export function Screenshot({ sessionId, path, alt }: { sessionId: string; path: string; alt: string }) {
  const url = useFileUrl(sessionId, path);
  if (url === "failed") return <div className="empty small">Screenshot unavailable ({path}).</div>;
  if (!url) return <div className="empty small">Loading screenshot…</div>;
  return (
    <a href={url} target="_blank" rel="noreferrer">
      <img className="shot" src={url} alt={alt} />
    </a>
  );
}
