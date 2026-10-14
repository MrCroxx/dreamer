import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  ImagePlus,
  Layers3,
  LoaderCircle,
  Plus,
  Printer,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  WandSparkles,
  X,
} from "lucide-react";
import { api, ApiError, download, token } from "./api";
import { activeStates, stateLabels, type Job, type Status } from "./types";
import { Viewer } from "./Viewer";
import { ModelOutput } from "./ModelOutput";
import { Conversation } from "./Conversation";
import { ColorControls } from "./ColorControls";
import "./studio.css";

const templates = [
  {
    id: "organizer",
    name: "Pocket organizer",
    tag: "EVERYDAY OBJECTS",
    prompt:
      "Design a 100 x 65 x 30 mm desk organizer with rounded corners, a 3 mm bottom and walls, and two compartments.",
    icon: Layers3,
  },
  {
    id: "bracket",
    name: "Mounting bracket",
    tag: "FUNCTIONAL PARTS",
    prompt:
      "Make an L-shaped mounting bracket, 60 mm wide and 40 mm deep, with a 45 mm upright and two 6 mm screw holes in the base.",
    icon: SlidersHorizontal,
  },
  {
    id: "planter",
    name: "Desktop planter",
    tag: "A LITTLE GREEN",
    prompt:
      "Create a cylindrical desktop planter, 72 mm diameter and 65 mm tall, with 3 mm walls and a 6 mm drainage hole.",
    icon: Box,
  },
  {
    id: "vase",
    name: "Contoured vase",
    tag: "CURVED FORMS",
    prompt:
      "Create a smooth open-top vase, 75 mm tall with 2 mm walls. Loft circular sections with radii 22, 28, 16, and 20 mm at heights 0, 20, 50, and 75 mm, then hollow it inward while preserving the base.",
    icon: WandSparkles,
  },
] as const;
type Template = (typeof templates)[number]["id"];

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("job"),
  );
  const [prompt, setPrompt] = useState<string>(() => {
    try {
      const saved = JSON.parse(
        sessionStorage.getItem("dreamer-draft") || "null",
      );
      const query = new URLSearchParams(window.location.search);
      return typeof saved?.prompt === "string" &&
        saved?.conversation === (query.get("conversation") || query.get("job"))
        ? saved.prompt || ""
        : "";
    } catch {
      return "";
    }
  });
  const [images, setImages] = useState<string[]>([]);
  const [conversationTip, setConversationTip] = useState<string | null>(() => {
    const query = new URLSearchParams(window.location.search);
    return query.get("conversation") || query.get("job");
  });
  const [demo, setDemo] = useState<Template | null>(null);
  const [pending, setPending] = useState(false);
  const [draftVisible, setDraftVisible] = useState(false);
  const [previewColor, setPreviewColor] = useState<string | null>(null);
  const [mobileTab, setMobileTab] = useState<"chat" | "preview">("chat");
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [confirm, setConfirm] = useState<"slice" | "print" | null>(null);
  const [plateConfirmed, setPlateConfirmed] = useState(false);
  const [apiToken, setApiToken] = useState(token());
  const [requiresToken, setRequiresToken] = useState(false);
  const [tab, setTab] = useState<"details" | "history" | "cad">("details");
  const imageInput = useRef<HTMLInputElement>(null);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const selected = jobs.find((job) => job.id === selectedId);
  const isBusy = !!selected && activeStates.has(selected.state);
  const creations = jobs.filter(
    (job) => !jobs.some((child) => child.parent_id === job.id),
  );
  const savedColor =
    selected?.plan?.color ||
    jobs.find((job) => job.id === selected?.parent_id)?.plan?.color ||
    "#dd7652";
  useEffect(() => {
    setPreviewColor(null);
  }, [selectedId]);
  useEffect(() => {
    try {
      sessionStorage.setItem(
        "dreamer-draft",
        JSON.stringify({ conversation: conversationTip, prompt }),
      );
    } catch {
      /* Draft storage is optional. */
    }
    if (promptInput.current) {
      promptInput.current.style.height = "auto";
      promptInput.current.style.height = `${Math.min(160, Math.max(76, promptInput.current.scrollHeight))}px`;
    }
  }, [prompt, conversationTip]);

  async function saveColor(color: string) {
    if (!selected || isBusy || pending) return;
    setPending(true);
    setError("");
    try {
      const job = await api<Job>(`/jobs/${selected.id}/appearance`, { color });
      setJobs((previous) => [job, ...previous]);
      setSelectedId(job.id);
      setConversationTip(job.id);
      setPreviewColor(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextJobs] = await Promise.all([
        api<Status>("/status"),
        api<Job[]>("/jobs"),
      ]);
      setStatus(nextStatus);
      setRequiresToken(nextStatus.auth_required);
      if (selectedId && !nextJobs.some((job) => job.id === selectedId)) {
        nextJobs.push(await api<Job>(`/jobs/${selectedId}`));
      }
      setJobs(nextJobs);
      setConnectionError("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setRequiresToken(true);
      setConnectionError((err as Error).message);
    }
  }, [selectedId]);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2200);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    setConfirm(null);
    setPlateConfirmed(false);
    const url = new URL(window.location.href);
    if (selectedId) url.searchParams.set("job", selectedId);
    else url.searchParams.delete("job");
    if (conversationTip && conversationTip !== selectedId)
      url.searchParams.set("conversation", conversationTip);
    else url.searchParams.delete("conversation");
    window.history.replaceState(null, "", url);
  }, [selectedId, conversationTip]);

  async function generate() {
    if (
      !prompt.trim() ||
      pending ||
      isBusy ||
      (selectedId && !selected) ||
      (!demo && !status?.llm_ready)
    )
      return;
    setPending(true);
    setError("");
    try {
      const job = await api<Job>("/jobs", {
        prompt,
        images,
        parent_id: selected?.id || null,
        demo_template: demo,
      });
      setJobs((previous) => [job, ...previous]);
      setSelectedId(job.id);
      setConversationTip(job.id);
      setTab("details");
      setPrompt((current) => (current === prompt ? "" : current));
      setDemo(null);
      setImages([]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files) return;
    setError("");
    if (files.length + images.length > 3) {
      setError("Add up to three reference images.");
      return;
    }
    try {
      const additions = await Promise.all(
        Array.from(files).map(
          (file) =>
            new Promise<string>((resolve, reject) => {
              if (
                !["image/png", "image/jpeg", "image/webp"].includes(
                  file.type,
                ) ||
                file.size > 5_000_000
              ) {
                reject(
                  new Error("Use PNG, JPEG or WebP images smaller than 5 MB."),
                );
                return;
              }
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result));
              reader.onerror = () => reject(new Error("Unable to read image"));
              reader.readAsDataURL(file);
            }),
        ),
      );
      setImages((previous) => [...previous, ...additions]);
      setDemo(null);
    } catch (err) {
      setError((err as Error).message);
    }
    if (imageInput.current) imageInput.current.value = "";
  }

  function newModel() {
    setSelectedId(null);
    setConversationTip(null);
    setDemo(null);
    setPrompt("");
    setImages([]);
    setError("");
    setMobileTab("chat");
    promptInput.current?.focus();
  }
  function selectVersion(id: string, openConversation = false) {
    setSelectedId(id);
    if (openConversation) {
      setConversationTip(id);
      setPrompt("");
      setImages([]);
    }
    setDemo(null);
    setError("");
  }
  function chooseTemplate(
    template: (typeof templates)[number],
    useDemo = false,
  ) {
    setPrompt(template.prompt);
    setImages([]);
    setSelectedId(null);
    setConversationTip(null);
    setDemo(useDemo ? template.id : null);
    promptInput.current?.focus();
  }
  async function approve() {
    if (!selected || !confirm || pending) return;
    const action = confirm;
    setPending(true);
    setError("");
    try {
      const next = await api<Job>(`/jobs/${selected.id}/${action}`, {
        confirmed: true,
        artifact_sha256:
          action === "slice" ? selected.model_sha256 : selected.slice_sha256,
      });
      setJobs((previous) =>
        previous.map((job) => (job.id === next.id ? next : job)),
      );
      setConfirm(null);
    } catch (err) {
      setError((err as Error).message);
      setConfirm(null);
    } finally {
      setPending(false);
    }
  }
  function exportFile(filename: string) {
    if (selected)
      void download(selected.id, filename).catch((err) =>
        setError(err.message),
      );
  }

  function focusComposer() {
    setMobileTab("chat");
    requestAnimationFrame(() => promptInput.current?.focus());
  }

  return (
    <div className={`app studio mobile-${mobileTab}`}>
      <aside className="sidebar">
        <a
          className="brand"
          href="/"
          onClick={(event) => {
            event.preventDefault();
            newModel();
          }}
        >
          <span className="brand-mark">
            d<span>●</span>
          </span>
          dreamer<span className="beta">BETA</span>
        </a>
        <div className="workspace-label">
          YOUR WORKSPACE <span>01</span>
        </div>
        <button className="new-model" onClick={newModel}>
          <Plus size={17} /> New creation <span>↗</span>
        </button>
        <div className="nav-title">
          <Clock3 size={14} /> Recent creations{" "}
          <span>{creations.length.toString().padStart(2, "0")}</span>
        </div>
        <div className="job-list">
          {jobs.length ? (
            creations.map((job) => (
              <button
                key={job.id}
                className={`job-item ${job.id === conversationTip ? "selected" : ""}`}
                onClick={() => {
                  selectVersion(job.id, true);
                }}
              >
                <span className="job-icon">
                  <Box size={16} />
                </span>
                <span className="job-text">
                  <strong>{job.name}</strong>
                  <small>{stateLabels[job.state]}</small>
                </span>
                {activeStates.has(job.state) ? (
                  <LoaderCircle className="spin" size={13} />
                ) : (
                  <span
                    className={`tiny-dot ${job.state === "failed" || job.state === "send_unknown" ? "red" : ""}`}
                  />
                )}
              </button>
            ))
          ) : (
            <p className="history-empty">
              A blank canvas, for now.
              <br />
              Your creations will live here.
            </p>
          )}
        </div>
        <div className="sidebar-bottom">
          <button
            type="button"
            className="printer-card"
            aria-label="Printer configuration"
            onClick={() => setSettingsOpen(true)}
          >
            <span className="printer-symbol">
              <Printer size={24} strokeWidth={1.3} />
            </span>
            <span>
              <strong>Bambu Lab A1 mini</strong>
              <span className="printer-status">
                <i className={`dot ${status?.print_ready ? "green" : ""}`} />
                {status?.print_ready ? "Dispatch configured" : "Not connected"}
              </span>
            </span>
            <ChevronRight className="printer-chevron" size={16} />
          </button>
          <button
            className="sidebar-link"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 size={16} /> Connections & settings
          </button>
          <button className="sidebar-link" onClick={() => setHelpOpen(true)}>
            <CircleHelp size={16} /> A little guidance <span>↗</span>
          </button>
          <div className="local-badge">
            <span className="dot green" /> LOCAL WORKSPACE <span>v0.1</span>
          </div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div className="breadcrumb">
            <h1>{selected?.name || "New creation"}</h1>
            {selected && (
              <span className={`workspace-state ${isBusy ? "working" : ""}`}>
                {isBusy && <LoaderCircle size={13} className="spin" />}
                {stateLabels[selected.state]}
              </span>
            )}
          </div>
          <div className="topbar-right">
            <span className="local-pill">
              <span className="dot green" /> Built locally
            </span>
            <button
              className="avatar"
              title="Open settings"
              aria-label="Open settings"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings2 size={17} />
            </button>
          </div>
        </header>
        <div className="page-content">
          <label className="mobile-history">
            Recent creations
            <select
              aria-label="Select a recent creation"
              value={selectedId || ""}
              onChange={(event) =>
                event.target.value
                  ? selectVersion(event.target.value, true)
                  : newModel()
              }
            >
              <option value="">New creation</option>
              {selected && !creations.some((job) => job.id === selected.id) && (
                <option value={selected.id}>
                  {selected.name} · Earlier version
                </option>
              )}
              {creations.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.name} · {stateLabels[job.state]}
                </option>
              ))}
            </select>
          </label>
          <div
            className="mobile-workspace-tabs"
            role="tablist"
            aria-label="Workspace panel"
          >
            <button
              role="tab"
              aria-selected={mobileTab === "chat"}
              onClick={() => setMobileTab("chat")}
            >
              Conversation
            </button>
            <button
              role="tab"
              aria-selected={mobileTab === "preview"}
              onClick={() => setMobileTab("preview")}
            >
              3D preview {isBusy && <span className="dot green" />}
            </button>
          </div>

          {connectionError && (
            <div role="alert" className="error-banner">
              {connectionError}
              <button onClick={() => setSettingsOpen(true)}>
                Check connection <ArrowRight size={13} />
              </button>
            </div>
          )}
          {error && (
            <div role="alert" className="error-banner">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={15} />
              </button>
            </div>
          )}

          <div className="workbench">
            <section className="composer-panel">
              <div className="chat-heading">
                <span className="chat-avatar">
                  <Sparkles size={18} />
                </span>
                <div>
                  <h2>Design with Dreamer</h2>
                  <p>
                    {selectedId
                      ? "Every change is saved as a version."
                      : "Describe it. Refine it. Make it."}
                  </p>
                </div>
              </div>
              {conversationTip && (
                <Conversation
                  tipId={conversationTip}
                  selectedId={selectedId}
                  refreshKey={
                    jobs.find((job) => job.id === conversationTip)?.updated_at
                  }
                  onSelect={(id) => selectVersion(id)}
                />
              )}
              {!conversationTip && (
                <div className="chat-welcome">
                  <h3>What would you like to make?</h3>
                  <p>
                    Describe an object, add measurements, or attach a reference.
                    Start with an idea below.
                  </p>
                  <div className="ideas-header">
                    <span>A SPARK TO START WITH</span>
                    <span>↙</span>
                  </div>
                  <div className="template-list">
                    {templates.map((template) => (
                      <div className="template-card" key={template.id}>
                        <button
                          className="template-main"
                          onClick={() =>
                            chooseTemplate(template, !status?.llm_ready)
                          }
                        >
                          <span className={`template-art ${template.id}`}>
                            <template.icon size={24} strokeWidth={1.2} />
                          </span>
                          <span>
                            <small>{template.tag}</small>
                            <strong>{template.name}</strong>
                          </span>
                          <ArrowRight size={14} />
                        </button>
                        <button
                          className="try-demo"
                          onClick={() => chooseTemplate(template, true)}
                        >
                          Try demo
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="chat-composer">
                {selectedId && (
                  <div className="revision-banner">
                    <span>
                      {selectedId !== conversationTip
                        ? "Editing an earlier version"
                        : isBusy
                          ? "Your model is being created"
                          : "Continue from the version in preview"}
                    </span>
                    {selectedId !== conversationTip && conversationTip && (
                      <button onClick={() => selectVersion(conversationTip)}>
                        Back to latest
                      </button>
                    )}
                  </div>
                )}
                <div
                  className={`prompt-box ${demo ? "demo-prompt" : ""}`}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    void upload(event.dataTransfer.files);
                  }}
                >
                  <textarea
                    ref={promptInput}
                    aria-label="Describe your model"
                    placeholder={
                      selectedId
                        ? "Make it blue, add a handle, change the dimensions…"
                        : "Describe your model, dimensions, and color…"
                    }
                    value={prompt}
                    maxLength={8000}
                    onChange={(event) => {
                      setPrompt(event.target.value);
                      setDemo(null);
                    }}
                    onKeyDown={(event) => {
                      if (
                        event.key === "Enter" &&
                        !event.shiftKey &&
                        !event.nativeEvent.isComposing
                      ) {
                        event.preventDefault();
                        void generate();
                      }
                    }}
                  />
                  {images.length > 0 && (
                    <div className="image-list">
                      {images.map((image, i) => (
                        <div key={i}>
                          <img src={image} alt={`Reference ${i + 1}`} />
                          <button
                            aria-label={`Remove reference ${i + 1}`}
                            onClick={() =>
                              setImages((previous) =>
                                previous.filter((_, index) => index !== i),
                              )
                            }
                          >
                            <X size={11} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="prompt-footer">
                    <button onClick={() => imageInput.current?.click()}>
                      <ImagePlus size={15} /> Add reference
                    </button>
                    <span>
                      {images.length
                        ? `${images.length}/3 images`
                        : "PNG, JPG, WEBP"}
                    </span>
                  </div>
                </div>
                <input
                  ref={imageInput}
                  hidden
                  type="file"
                  multiple
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => void upload(event.target.files)}
                />
                <div className="composer-actions">
                  <button
                    type="button"
                    className="model-select"
                    aria-label="Configure model"
                    onClick={() => setSettingsOpen(true)}
                  >
                    <span className="model-icon">
                      <WandSparkles size={14} />
                    </span>
                    <span>
                      {demo
                        ? "Demo template · fixed dimensions"
                        : status?.llm_ready
                          ? status.model
                          : "Connect a model to get started"}
                    </span>
                    <ChevronDown className="model-chevron" size={13} />
                  </button>
                  <button
                    className="primary generate-button"
                    disabled={
                      !prompt.trim() ||
                      pending ||
                      isBusy ||
                      (!!selectedId && !selected) ||
                      (!!demo && !!images.length) ||
                      (!demo && !status?.llm_ready)
                    }
                    onClick={() => void generate()}
                  >
                    {pending ? (
                      <LoaderCircle className="spin" size={17} />
                    ) : (
                      <Sparkles size={17} />
                    )}
                    {demo
                      ? "Build demo model"
                      : selectedId
                        ? "Send changes"
                        : "Generate model"}
                    <ArrowRight size={17} />
                  </button>
                </div>
                <div className="prompt-hint" role="status">
                  {demo
                    ? "Fixed demo · No model call"
                    : isBusy
                      ? "Runs in the background. You can draft your next message."
                      : "Enter to send · Shift + Enter for a new line"}
                </div>
              </div>
            </section>

            <section className="preview-panel">
              <div className="preview-heading">
                <div className="section-label">
                  <span>
                    <Box size={16} /> Model preview
                  </span>
                </div>
                {selected && (
                  <button className="preview-chat-link" onClick={focusComposer}>
                    Ask for a change <ArrowRight size={14} />
                  </button>
                )}
              </div>
              <Viewer
                jobId={selected?.id}
                modelHash={selected?.model_sha256}
                color={previewColor || savedColor}
                buildVolume={status?.build_volume || [180, 180, 180]}
                busy={isBusy}
                onDraftChange={setDraftVisible}
                live={
                  selected?.state === "generating" ||
                  selected?.state === "building"
                }
              />
              <div className="model-info">
                <div className="model-title">
                  <span className="object-icon">
                    <Box size={20} strokeWidth={1.4} />
                  </span>
                  <div>
                    <h3>
                      {selected?.name || "Your next creation"}
                      {selected?.demo && (
                        <span className="demo-badge">DEMO</span>
                      )}
                    </h3>
                    <p>
                      {selected
                        ? stateLabels[selected.state]
                        : "Waiting for a little inspiration"}
                    </p>
                  </div>
                  {selected?.model_sha256 && (
                    <div className="download-buttons">
                      <button
                        title="Download STL"
                        aria-label="Download STL"
                        onClick={() => exportFile("model.stl")}
                      >
                        <ArrowDownToLine size={14} /> STL
                      </button>
                      <button
                        title="Download STEP"
                        aria-label="Download STEP"
                        onClick={() => exportFile("model.step")}
                      >
                        <ArrowDownToLine size={14} /> STEP
                      </button>
                    </div>
                  )}
                </div>
                <div className="spec-grid">
                  <div>
                    <span>DIMENSIONS</span>
                    <strong>
                      {selected?.geometry
                        ? selected.geometry.dimensions
                            .map((d) => Number(d.toFixed(1)))
                            .join(" × ")
                        : "—"}
                      <small> mm</small>
                    </strong>
                  </div>
                  <div>
                    <span>SOLID VOLUME</span>
                    <strong>
                      {selected?.geometry
                        ? (selected.geometry.volume_mm3 / 1000).toFixed(1)
                        : "—"}
                      <small> cm³</small>
                    </strong>
                  </div>
                  <div>
                    <span>BUILD PLATE</span>
                    <strong
                      className={
                        selected?.geometry?.fits_build_volume === false
                          ? "warning-text"
                          : ""
                      }
                    >
                      {selected?.geometry
                        ? selected.geometry.fits_build_volume
                          ? "Fits A1 mini"
                          : "Exceeds build volume"
                        : "180 × 180"}
                      {!selected?.geometry && <small> mm</small>}
                    </strong>
                  </div>
                </div>
              </div>
              {selected?.model_sha256 && (
                <ColorControls
                  key={`${selected.id}-${savedColor}`}
                  color={savedColor}
                  disabled={pending || isBusy}
                  onPreview={setPreviewColor}
                  onSave={saveColor}
                />
              )}
              {selected && (
                <details className="model-inspector">
                  <summary>
                    Model details & activity <ChevronDown size={15} />
                  </summary>
                  <section className="details-panel">
                    <div className="details-tabs">
                      <button
                        className={tab === "details" ? "active" : ""}
                        onClick={() => setTab("details")}
                      >
                        <SlidersHorizontal size={14} /> Design notes
                      </button>
                      <button
                        className={tab === "history" ? "active" : ""}
                        onClick={() => setTab("history")}
                      >
                        <Clock3 size={14} /> Activity
                      </button>
                      <button
                        className={tab === "cad" ? "active" : ""}
                        onClick={() => setTab("cad")}
                      >
                        <Code2 size={14} /> CAD plan
                      </button>
                      {selected?.plan && (
                        <button
                          className="refine-button"
                          onClick={() => {
                            setDemo(null);
                            promptInput.current?.focus();
                          }}
                        >
                          <WandSparkles size={14} /> Refine this model{" "}
                          <ArrowRight size={13} />
                        </button>
                      )}
                    </div>
                    {selected?.error && (
                      <div role="alert" className="job-error">
                        {selected.error}
                      </div>
                    )}
                    {tab === "details" && (
                      <div className="design-notes">
                        {selected?.plan ? (
                          <>
                            <p>{selected.plan.description}</p>
                            <ul>
                              {selected.plan.assumptions.map(
                                (assumption, i) => (
                                  <li key={i}>{assumption}</li>
                                ),
                              )}
                            </ul>
                            <div className="original-prompt">
                              <span>YOUR BRIEF</span>
                              <p>{selected.prompt}</p>
                            </div>
                          </>
                        ) : (
                          <>
                            <span className="note-asterisk">✳</span>
                            <div>
                              <strong>
                                Good things take shape, one detail at a time.
                              </strong>
                              <p>
                                Your model's dimensions, design decisions, and
                                assumptions will appear here.
                              </p>
                            </div>
                            <span className="cad-note">
                              PARAMETRIC BY NATURE.
                            </span>
                          </>
                        )}
                      </div>
                    )}
                    {tab === "history" && (
                      <div className="activity-list">
                        {selected ? (
                          selected.events.map((event, i) => (
                            <div key={i}>
                              <Check size={13} />
                              <span>{event.message}</span>
                              <time>
                                {new Date(event.at).toLocaleTimeString()}
                              </time>
                            </div>
                          ))
                        ) : (
                          <p>Your creation's journey will appear here.</p>
                        )}
                      </div>
                    )}
                    {tab === "cad" && (
                      <div className="cad-content">
                        {selected?.plan ? (
                          <>
                            <button onClick={() => exportFile("plan.json")}>
                              <ArrowDownToLine size={14} /> Download CAD plan
                            </button>
                            <pre>{JSON.stringify(selected.plan, null, 2)}</pre>
                          </>
                        ) : (
                          <p>
                            Every creation has an editable, structured CAD plan.
                          </p>
                        )}
                      </div>
                    )}
                    {selected?.slice_sha256 && (
                      <button
                        className="download-slice"
                        onClick={() => exportFile("print.gcode.3mf")}
                      >
                        <ArrowDownToLine size={14} /> Download sliced 3MF
                      </button>
                    )}
                  </section>
                </details>
              )}
              {selected && (
                <ModelOutput
                  key={selected.id}
                  jobId={selected.id}
                  demo={selected.demo}
                />
              )}

              <div className="make-row">
                <div>
                  <span className="step-dot">3</span>
                  <div>
                    <strong>
                      {selected?.state === "sliced"
                        ? "Ready to make it real?"
                        : selected?.state === "submitted"
                          ? "Your creation is on its way."
                          : "Happy with the shape?"}
                    </strong>
                    <p>
                      {selected?.state === "sliced"
                        ? "Confirm your printer, material, and clear build plate."
                        : selected?.state === "submitted"
                          ? "Command submitted. Follow live progress on your printer."
                          : "Review your model, then prepare it for printing."}
                    </p>
                  </div>
                </div>
                <button
                  className="primary make-button"
                  disabled={
                    pending ||
                    draftVisible ||
                    !selected ||
                    (selected.state !== "preview" &&
                      selected.state !== "sliced") ||
                    (selected.state === "preview" &&
                      (!status?.slicer_ready ||
                        !selected.geometry?.fits_build_volume)) ||
                    (selected.state === "sliced" && !status?.print_ready)
                  }
                  onClick={() => {
                    setPlateConfirmed(false);
                    setConfirm(
                      selected?.state === "sliced" ? "print" : "slice",
                    );
                  }}
                >
                  {isBusy ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Printer size={16} />
                  )}
                  {selected?.state === "sliced"
                    ? "Send to printer"
                    : "Approve & slice"}
                  <ArrowRight size={16} />
                </button>
              </div>
              {!status?.slicer_ready && (
                <button
                  className="setup-hint"
                  onClick={() => setSettingsOpen(true)}
                >
                  <Settings2 size={12} /> Connect Bambu Studio to enable slicing{" "}
                  <ArrowRight size={12} />
                </button>
              )}
            </section>
          </div>
        </div>
      </main>

      {settingsOpen && (
        <Modal
          title="Your local connections"
          onClose={() => setSettingsOpen(false)}
        >
          <p className="modal-description">
            Model and printer configuration live in the backend's .env file.
            Keys never reach the browser.
          </p>
          <div className="connection-item">
            <WandSparkles size={19} />
            <div>
              <strong>OpenAI-compatible model</strong>
              <p>{status?.model || "Unavailable"}</p>
            </div>
            <span
              className={`connection-state ${status?.llm_ready ? "ready" : ""}`}
            >
              {status?.llm_ready ? "Configured" : "Not configured"}
            </span>
          </div>
          <div className="connection-item">
            <Layers3 size={19} />
            <div>
              <strong>Bambu Studio CLI</strong>
              <p>
                {status?.slicing_presets?.join(" · ") ||
                  "Machine, process & filament presets"}
              </p>
            </div>
            <span
              className={`connection-state ${status?.slicer_ready ? "ready" : ""}`}
            >
              {status?.slicer_ready ? "Configured" : "Setup needed"}
            </span>
          </div>
          <div className="connection-item">
            <Printer size={19} />
            <div>
              <strong>{status?.printer || "A1 mini"}</strong>
              <p>
                {status?.build_volume.join(" × ") || "180 × 180 × 180"} mm ·{" "}
                {status?.filament_slot || "A1"}
              </p>
            </div>
            <span
              className={`connection-state ${status?.print_ready ? "ready" : ""}`}
            >
              {status?.print_ready ? "Enabled" : "Disabled"}
            </span>
          </div>
          <p className="settings-note">
            Configured means settings are present, not that a live device
            connection has been verified. See README.md for setup and printer
            compatibility.
          </p>
          {requiresToken ? (
            <>
              <label className="token-label" htmlFor="access-token">
                Workspace access token
              </label>
              <input
                id="access-token"
                type="password"
                value={apiToken}
                onChange={(event) => setApiToken(event.target.value)}
                placeholder="Enter the workspace access token"
              />
              <button
                className="primary modal-primary"
                onClick={() => {
                  sessionStorage.setItem("dreamer-token", apiToken);
                  void refresh();
                  setSettingsOpen(false);
                }}
              >
                Save & reconnect <ArrowRight size={16} />
              </button>
            </>
          ) : (
            <button
              className="primary modal-primary"
              onClick={() => setSettingsOpen(false)}
            >
              Done
            </button>
          )}
        </Modal>
      )}
      {helpOpen && (
        <Modal
          title="From idea to first layer"
          onClose={() => setHelpOpen(false)}
        >
          <ol className="help-steps">
            <li>
              <strong>Describe the object.</strong>
              <p>
                Include dimensions, wall thickness, and how you'll use it.
                Reference images need a vision-capable model.
              </p>
            </li>
            <li>
              <strong>Explore the shape.</strong>
              <p>
                Orbit the real CAD model, check its dimensions, and use Refine
                to create a new revision. Originals are preserved.
              </p>
            </li>
            <li>
              <strong>Approve, slice, then print.</strong>
              <p>
                Bambu Studio uses your actual printer and material profiles.
                Sending requires another explicit confirmation.
              </p>
            </li>
          </ol>
          <p className="settings-note">
            Best for functional parts and geometric objects. A photo alone
            cannot establish accurate real-world dimensions. Demo templates use
            fixed designs and never interpret your prompt.
          </p>
        </Modal>
      )}
      {confirm && selected && (
        <Modal
          title={confirm === "slice" ? "Approve this model?" : "Make it real?"}
          onClose={() => {
            if (!pending) setConfirm(null);
          }}
        >
          <p className="modal-description">
            {confirm === "slice"
              ? "Bambu Studio will slice this exact model using your configured printer, process, and filament presets."
              : "This sends the sliced file to your physical printer and starts a print after the printer checks pass."}
          </p>
          <div className="approval-summary">
            <strong>{selected.name}</strong>
            <span>{selected.geometry?.dimensions.join(" × ")} mm</span>
            <span>{status?.printer}</span>
            {confirm === "slice" &&
              status?.slicing_presets?.map((preset) => (
                <span key={preset}>{preset}</span>
              ))}
            <span>
              {status?.bed_type} · Filament slot {status?.filament_slot}
            </span>
          </div>
          {confirm === "print" && (
            <label className="confirmation-check">
              <input
                type="checkbox"
                checked={plateConfirmed}
                onChange={(event) => setPlateConfirmed(event.target.checked)}
              />
              I checked the clear build plate, loaded filament, nozzle, and
              printer settings.
            </label>
          )}
          <div className="modal-actions">
            <button onClick={() => setConfirm(null)} disabled={pending}>
              Go back
            </button>
            <button
              className="primary"
              disabled={pending || (confirm === "print" && !plateConfirmed)}
              onClick={() => void approve()}
            >
              {pending ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Check size={16} />
              )}
              {confirm === "slice"
                ? "Approve & slice"
                : "Confirm & start print"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="modal-heading">
        <h2>{title}</h2>
        <button aria-label="Close dialog" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
