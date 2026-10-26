// The "Add Arabic names" answer of a campus whose names all have an Arabic form: the starter campus mocks serve it so the
// dialog's Arabic names section shows "nothing to add" instead of an error (docs/ANUM_MINE_STARTER_CAMPUS.md#add-arabic-names).
export const ARABIC_NAMES_NONE = {
  version: 1, catalog_version: 1, workspace_id: 'workspace_foundation', payload_hash: 'c'.repeat(64), can_confirm: true,
  items: [], counts: { buildings: 0, floors: 0, rooms: 0 }, total: 0,
  rules: { overwrites: false, fills_empty_only: true, matches_master_names_only: true, changes_other_fields: false },
};
