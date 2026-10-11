import { useEffect, useState } from 'react';
import { getCurrentMembership } from './lib/api';
import { isActiveOwner } from './lib/capabilities';

/** Whether the caller is an active owner of this workspace; null while the membership loads. */
export function useIsOwner(): boolean | null {
  const [owner, setOwner] = useState<boolean | null>(null);
  useEffect(() => { void getCurrentMembership().then((m) => setOwner(isActiveOwner(m))).catch(() => setOwner(false)); }, []);
  return owner;
}
