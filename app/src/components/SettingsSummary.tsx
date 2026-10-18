import type { Pipeline, Preset, Settings } from "../api/types";

/** The three-line "what will actually happen" readout — Wizard step 3's
 * summary and Convertir's header are required to say exactly the same
 * thing (see docs/desktop-app.md and the wizard UX bug this fixes: the
 * wizard used to promise one output folder/preset and Convertir silently
 * used another). Both read the same `Settings` + resolved `Preset`, never a
 * client-side re-derivation. */
export function outputLine(settings: Settings): string {
  if (settings.default_output_mode === "fixed" && settings.default_output_dir) {
    return `Siempre en ${settings.default_output_dir}`;
  }
  return "Junto al original (carpeta hermana _2markdown)";
}

function shortModel(id: string | null): string {
  if (!id) return "ninguno";
  const parts = id.split(":");
  return parts.length > 1 ? parts.slice(1).join(":") : id;
}

export function presetLine(preset: Preset | undefined, pipeline: Pipeline | undefined): string {
  if (!preset || !pipeline) return "—";
  const ocr = shortModel(pipeline.ocr_model);
  const figures = pipeline.describe_figures ? shortModel(pipeline.figure_model) : "sin describir";
  const review = pipeline.review_model ? shortModel(pipeline.review_model) : "sin revisión";
  return `${preset.name} — ${ocr} / ${figures} / ${review}`;
}

export function modeLine(settings: Settings): string {
  return settings.default_mode === "sync" ? "Mantener sincronizada" : "Una vez";
}

export default function SettingsSummary({
  settings,
  presets,
  activePresetId,
}: {
  settings: Settings;
  presets: Preset[];
  /** The preset actually selected for this run (e.g. Convertir's radio
   * group) — N16: "Convertiré con:" must reflect that, not silently fall
   * back to the default preset while another one is selected. Omit to fall
   * back to `settings.default_preset_id` (e.g. the wizard's own step 4,
   * where there is no separate per-run selection). */
  activePresetId?: string;
}) {
  const preset = presets.find((p) => p.id === (activePresetId ?? settings.default_preset_id));
  return (
    <div className="settings-summary">
      <div>
        <b>Guardaré en:</b> {outputLine(settings)}
      </div>
      <div>
        <b>Convertiré con:</b> {presetLine(preset, preset?.pipeline)}
      </div>
      <div>
        <b>Modo:</b> {modeLine(settings)}
      </div>
    </div>
  );
}
