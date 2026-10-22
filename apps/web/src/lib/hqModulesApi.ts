/** Browser loaders for the module panels. They only read; each answers a ModuleState. */
import * as commerce from './commerceApi.ts';
import * as tyre from './tyreApi.ts';
import { ApiError } from './errors.ts';
import { commerceLive, moduleErrorState, tyreLive, type CommerceLive, type ModuleState, type TyreLive } from './hqModules.ts';

const failure = (error: unknown): ModuleState<never> => {
  if (error instanceof ApiError) return moduleErrorState(error.status, error.message);
  return moduleErrorState(null, error instanceof Error ? error.message : 'The request failed.');
};

const localDay = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export async function loadCommerceState(): Promise<ModuleState<CommerceLive>> {
  try {
    const setup = await commerce.getSetup();
    if (!setup.connectors) return { state: 'not_connected' };
    const day = localDay();
    return { state: 'ready', data: commerceLive(await commerce.getDashboard(day, day)) };
  } catch (error) {
    return failure(error);
  }
}

export async function loadTyreState(): Promise<ModuleState<TyreLive>> {
  try {
    const setup = await tyre.getSetup();
    if (!setup.connection) return { state: 'not_connected' };
    return { state: 'ready', data: tyreLive(await tyre.getDashboard()) };
  } catch (error) {
    return failure(error);
  }
}
