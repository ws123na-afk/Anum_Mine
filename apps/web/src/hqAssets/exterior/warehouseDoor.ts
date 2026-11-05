/**
 * Where the warehouse's entrance stands (`warehouse.ts`): the door of the glazed office annex at the left end of the front. Pure and
 * import free, so the campus layout (`campusModel.layoutCampus`) can aim a warehouse's lean at it without loading the asset kit.
 */

/** The shed's front width: the asset's own range (24 to 72 m, 42 when none is given). */
export const warehouseWidth = (width: number | undefined): number =>
  Math.min(72, Math.max(24, Number.isFinite(width as number) ? (width as number) : 42));

/** Width of the office annex at the left end of the front. */
export const warehouseAnnexWidth = (shedWidth: number): number => Math.min(10, shedWidth * 0.24);

/** The entrance's offset along the front from the building's middle (metres, the asset's own frame: front on +z, so negative is left). */
export function warehouseEntranceX(width: number | undefined): number {
  const w = warehouseWidth(width);
  return -w / 2 + warehouseAnnexWidth(w) / 2;
}
