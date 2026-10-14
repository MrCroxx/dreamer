import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUpRight,
  Box,
  Check,
  LoaderCircle,
  Sparkles,
} from "lucide-react";
import { api } from "./api";
import { activeStates, stateLabels, type Job } from "./types";

type Turn = Pick<
  Job,
  "id" | "parent_id" | "prompt" | "name" | "state" | "error"
> & {
  summary: string | null;
  color?: string | null;
  geometry?: Job["geometry"];
};

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
  const [atBottom, setAtBottom] = useState(true);

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
    <div className="conversation-area">
      <div
        className="conversation"
        role="region"
        aria-label="Design conversation"
        ref={viewport}
        tabIndex={0}
        onScroll={() => {
          const element = viewport.current;
          if (element)
            setAtBottom(
              element.scrollHeight - element.scrollTop - element.clientHeight <
                60,
            );
        }}
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
              <div className="assistant-label">
                <Sparkles size={14} />
                <strong>Dreamer</strong>
                {activeStates.has(turn.state) && (
                  <LoaderCircle size={13} className="spin" />
                )}
              </div>
              <p>
                {turn.summary ||
                  (activeStates.has(turn.state)
                    ? turn.state === "building"
                      ? "Checking the model and preparing your preview…"
                      : "Designing your model. You can keep writing while I work."
                    : "This version needs another try. Tell me what to change below.")}
              </p>
              {turn.error && (
                <details className="turn-error">
                  <summary>What went wrong</summary>
                  <p>{turn.error}</p>
                </details>
              )}
              <button
                className="version-card"
                aria-label={`View version ${index + 1}`}
                aria-pressed={selectedId === turn.id}
                onClick={() => onSelect(turn.id)}
              >
                <span
                  className="version-thumbnail"
                  style={{ color: turn.color || "#dd7652" }}
                >
                  <Box size={25} strokeWidth={1.5} />
                </span>
                <span className="version-copy">
                  <strong>
                    Version {index + 1}{" "}
                    <span>
                      {selectedId === turn.id
                        ? "In preview"
                        : stateLabels[turn.state]}
                    </span>
                  </strong>
                  <small>
                    {turn.geometry
                      ? `${turn.geometry.dimensions.map((n) => Number(n.toFixed(1))).join(" × ")} mm`
                      : turn.name}
                  </small>
                </span>
                {selectedId === turn.id ? (
                  <Check size={15} />
                ) : (
                  <ArrowUpRight size={15} />
                )}
              </button>
            </div>
          </div>
        ))}
      </div>
      {!atBottom && (
        <button
          className="latest-message"
          onClick={() => {
            viewport.current?.scrollTo({
              top: viewport.current.scrollHeight,
              behavior: "smooth",
            });
          }}
        >
          <ArrowDown size={14} /> Latest message
        </button>
      )}
    </div>
  );
}
