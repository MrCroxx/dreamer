import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { activeStates, stateLabels, type Job } from "./types";

type Turn = Pick<
  Job,
  "id" | "parent_id" | "prompt" | "name" | "state" | "error"
> & { summary: string | null };

export function Conversation({
  tipId,
  selectedId,
  refreshKey,
  onSelect,
}: {
  tipId: string;
  selectedId: string | null;
  refreshKey?: string;
  onSelect: (id: string) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  const lastTip = useRef("");

  useEffect(() => {
    let cancelled = false;
    const changed = lastTip.current !== tipId;
    if (changed) setTurns([]);
    void api<Turn[]>(`/jobs/${tipId}/conversation`)
      .then((next) => {
        if (cancelled) return;
        const element = viewport.current;
        const follow =
          changed ||
          !element ||
          element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        lastTip.current = tipId;
        setTurns(next);
        setError("");
        if (follow)
          requestAnimationFrame(() => {
            if (!cancelled && viewport.current)
              viewport.current.scrollTop = viewport.current.scrollHeight;
          });
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [tipId, refreshKey, retry]);

  return (
    <div
      className="conversation"
      role="region"
      aria-label="Design conversation"
      ref={viewport}
      tabIndex={0}
    >
      {error && (
        <p role="alert">
          Unable to load this conversation.{" "}
          <button onClick={() => setRetry(retry + 1)}>Retry</button>
        </p>
      )}
      {!turns.length && !error && <p>Loading conversation…</p>}
      {turns.map((turn, index) => (
        <div className="conversation-turn" key={turn.id}>
          <div className="conversation-user">
            <small>YOU</small>
            <p>{turn.prompt}</p>
          </div>
          <div className="conversation-assistant">
            <small>DREAMER · {stateLabels[turn.state]}</small>
            <p>
              {turn.summary ||
                (activeStates.has(turn.state)
                  ? "Working on this version. You can draft your next message below."
                  : turn.error ||
                    "This version has no completed model. Continue with a correction below.")}
            </p>
            <button
              aria-label={`View version ${index + 1}`}
              aria-pressed={selectedId === turn.id}
              onClick={() => onSelect(turn.id)}
            >
              Version {index + 1}
              {selectedId === turn.id ? " · Viewing" : " · View"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
