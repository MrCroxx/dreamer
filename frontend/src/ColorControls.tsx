import { useState } from "react";
import { Check, RotateCcw } from "lucide-react";

const colors = [
  ["Terracotta", "#dd7652"],
  ["Blue", "#2563eb"],
  ["Sage", "#76967a"],
  ["Ivory", "#e8e3d6"],
  ["Graphite", "#343941"],
  ["Lavender", "#9b87c9"],
];

export function ColorControls({
  color,
  disabled,
  onPreview,
  onSave,
}: {
  color: string;
  disabled: boolean;
  onPreview: (color: string | null) => void;
  onSave: (color: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(color);
  const dirty = draft.toLowerCase() !== color.toLowerCase();
  function preview(value: string) {
    setDraft(value);
    onPreview(value);
  }
  return (
    <div className="appearance-controls">
      <p className="appearance-note">
        Preview only. Printing uses the loaded filament.
      </p>
      <div
        className="appearance-options"
        role="group"
        aria-label="Preview color"
      >
        {colors.map(([name, value]) => (
          <button
            key={value}
            className="color-swatch"
            style={{ backgroundColor: value }}
            aria-label={`Preview in ${name}`}
            aria-pressed={draft.toLowerCase() === value}
            title={name}
            disabled={disabled}
            onClick={() => preview(value)}
          >
            {draft.toLowerCase() === value && (
              <Check size={16} color={name === "Ivory" ? "#343941" : "white"} />
            )}
          </button>
        ))}
        <label className="custom-color" title="Choose a custom preview color">
          <input
            type="color"
            aria-label="Custom preview color"
            value={draft}
            disabled={disabled}
            onChange={(event) => preview(event.target.value)}
          />
        </label>
        {dirty && (
          <div className="color-save">
            <button
              aria-label="Discard color change"
              title="Discard color change"
              onClick={() => {
                setDraft(color);
                onPreview(null);
              }}
              disabled={disabled}
            >
              <RotateCcw size={15} />
            </button>
            <button
              className="save-color"
              disabled={disabled}
              onClick={() => void onSave(draft)}
            >
              Save color
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
