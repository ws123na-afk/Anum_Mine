/** The button that opens the office history for a room or an agent. Kept apart so the panel itself stays a lazy chunk. */
export function HistoryLauncher({ label, onClick }: { label: string; onClick: () => void }) {
  return <button type="button" className="hqGhost hqHistLaunch" onClick={onClick}>{label}</button>;
}
