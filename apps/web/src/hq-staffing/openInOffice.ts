import { hqCommands } from '../lib/hqExtensions';

/** Opens the 3D headquarters at this office: the move waits until the headquarters is mounted (lib/hqExtensions.ts). */
export function openInOffice(name: string, go: (view: string) => void): void {
  hqCommands.keepPending({ building: name });
  go('office');
}
