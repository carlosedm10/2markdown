/** ArrowUp/ArrowDown/ArrowLeft/ArrowRight handling for a `role="radiogroup"`
 * of `role="radio"` items — moves both focus and selection, per the ARIA
 * radiogroup pattern. Space/Enter selection alone (the previous state here)
 * is reachable but doesn't behave like the radiogroup it announces itself
 * as. Each radio needs a `data-value` attribute the caller's `onSelect`
 * understands, and only the checked radio should carry `tabIndex={0}`
 * (`-1` on the rest) so Tab enters and leaves the group once instead of
 * stopping on every option. */
export function handleRadioGroupKeyDown(e: React.KeyboardEvent<HTMLElement>, onSelect: (value: string) => void): void {
  if (!["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"].includes(e.key)) return;
  const group = (e.currentTarget as HTMLElement).closest('[role="radiogroup"]');
  if (!group) return;
  const radios = Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]'));
  const idx = radios.indexOf(e.currentTarget as HTMLElement);
  if (idx === -1) return;
  e.preventDefault();
  const dir = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : -1;
  const next = radios[(idx + dir + radios.length) % radios.length];
  const value = next.dataset.value;
  if (value == null) return;
  onSelect(value);
  next.focus();
}
