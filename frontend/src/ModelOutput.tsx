import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { headers } from "./api";

type Entry = {
  kind: "attempt" | "reasoning" | "content" | "end" | "notice";
  text?: string;
  attempt?: number;
  status?: string;
};
type OutputPage = {
  entries: Entry[];
  offset: number;
  has_more: boolean;
  active: boolean;
  source: "recorded" | "saved_plan" | "unavailable";
};

function appendEntries(previous: Entry[], incoming: Entry[]) {
  const result = [...previous];
  for (const entry of incoming) {
    const last = result[result.length - 1];
    if (
      (entry.kind === "content" || entry.kind === "reasoning") &&
      last?.kind === entry.kind
    ) {
      result[result.length - 1] = {
        ...last,
        text: (last.text || "") + entry.text,
      };
    } else result.push(entry);
  }
  return result;
}

function label(entry: Entry) {
  if (entry.kind === "attempt") return `Attempt ${entry.attempt}`;
  if (entry.kind === "reasoning") return "Reasoning returned by the provider";
  if (entry.kind === "content") return "Model response";
  if (entry.kind === "end")
    return entry.status === "completed"
      ? "Attempt completed"
      : "Attempt interrupted";
  return "Recording notice";
}

export function ModelOutput({ jobId, demo }: { jobId: string; demo: boolean }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [source, setSource] = useState<OutputPage["source"]>("recorded");
  const [active, setActive] = useState(false);
  const [catchingUp, setCatchingUp] = useState(false);
  const [error, setError] = useState("");
  const [follow, setFollow] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let offset = 0;
    setEntries([]);
    setCatchingUp(true);
    setError("");
    async function poll() {
      let again = true;
      let delay = 1000;
      try {
        const response = await fetch(
          `/api/jobs/${jobId}/output?offset=${offset}`,
          {
            headers: headers(),
            signal: abort.signal,
            cache: "no-store",
          },
        );
        if (!response.ok)
          throw new Error("Unable to load model output. Retrying…");
        const page = (await response.json()) as OutputPage;
        if (abort.signal.aborted) return;
        offset = page.offset;
        setEntries((previous) =>
          page.source === "recorded"
            ? appendEntries(previous, page.entries)
            : page.entries,
        );
        setSource(page.source);
        setActive(page.active);
        setCatchingUp(page.has_more);
        setError("");
        again = page.active || page.has_more;
        if (page.has_more && page.entries.length) delay = 20;
      } catch (err) {
        if (!abort.signal.aborted) setError((err as Error).message);
      } finally {
        if (again && !abort.signal.aborted)
          timer = setTimeout(() => void poll(), delay);
      }
    }
    void poll();
    return () => {
      abort.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (follow && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [entries, follow]);

  function download() {
    const text = entries
      .map((entry) => `${label(entry)}\n${entry.text || ""}`)
      .join("\n\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "model-output.txt";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <section className="model-output">
      <div className="output-panel">
        <div className="output-toolbar">
          <span role="status">
            {catchingUp
              ? "Loading saved output"
              : active
                ? "Receiving model output"
                : "Saved output"}
          </span>
          <label>
            <input
              type="checkbox"
              checked={follow}
              onChange={(event) => setFollow(event.target.checked)}
            />{" "}
            Follow output
          </label>
          <button
            onClick={download}
            disabled={!entries.length || catchingUp}
            aria-label="Download model output"
          >
            <Download size={14} /> Download
          </button>
        </div>
        {demo && <p className="output-hint">Demo template; no model call.</p>}
        {source === "saved_plan" && (
          <p className="output-hint">
            The original stream was not recorded for this older job. Showing its
            saved CAD plan.
          </p>
        )}
        {source === "unavailable" && (
          <p className="output-hint">
            No recorded output yet. Older jobs cannot recover a stream that was
            not recorded.
          </p>
        )}
        {error && (
          <p role="alert" className="output-hint">
            {error}
          </p>
        )}
        <div
          className="output-log"
          role="region"
          ref={viewport}
          tabIndex={0}
          aria-label="Model output text"
          onScroll={() => {
            const element = viewport.current;
            if (element)
              setFollow(
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                  36,
              );
          }}
        >
          {!entries.length && (
            <p>
              {active ? "Waiting for model output…" : "No output recorded."}
            </p>
          )}
          {entries.map((entry, index) => (
            <div className={`output-entry ${entry.kind}`} key={index}>
              <strong>{label(entry)}</strong>
              {entry.text && <pre>{entry.text}</pre>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
