export type Job = {
  id: string;
  name: string;
  prompt: string;
  parent_id: string | null;
  demo: boolean;
  state:
    | "queued"
    | "generating"
    | "building"
    | "preview"
    | "slicing"
    | "sliced"
    | "sending"
    | "submitted"
    | "send_unknown"
    | "failed";
  created_at: string;
  updated_at: string;
  error: string | null;
  events: { at: string; message: string }[];
  plan: {
    name: string;
    description: string;
    assumptions: string[];
    operations: { id: string; op: string }[];
  } | null;
  geometry: {
    dimensions: number[];
    volume_mm3: number;
    solids: number;
    fits_build_volume: boolean;
  } | null;
  model_sha256: string | null;
  slice_sha256: string | null;
};
export type Status = {
  auth_required: boolean;
  llm_ready: boolean;
  model: string;
  slicer_ready: boolean;
  slicing_presets: string[];
  print_ready: boolean;
  printer: string;
  build_volume: number[];
  filament_slot: string;
  bed_type: string;
};

export const activeStates = new Set([
  "queued",
  "generating",
  "building",
  "slicing",
  "sending",
]);
export const stateLabels: Record<Job["state"], string> = {
  queued: "Queued",
  generating: "Designing",
  building: "Building geometry",
  preview: "Ready to review",
  slicing: "Slicing",
  sliced: "Ready to print",
  sending: "Sending to printer",
  submitted: "Submitted to printer",
  send_unknown: "Check your printer",
  failed: "Needs attention",
};
