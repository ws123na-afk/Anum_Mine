/** Build-time switch for the ANUM MINE headquarters route (#office-preview) and its sidebar link.
 *
 * `VITE_ANUM_HQ=off` hides both and sends a stale #office-preview link to the normal app, so a bad
 * release of the office can be switched off by one rebuild without touching the rest of ANUM OS.
 * Any other value (or none) keeps it on. See docs/ANUM_MINE_HQ_DEPLOYMENT_AND_ROLLBACK.md.
 */
export function headquartersEnabled(value: string | undefined = import.meta.env?.VITE_ANUM_HQ as string | undefined): boolean {
  return (value ?? '').trim().toLowerCase() !== 'off';
}
