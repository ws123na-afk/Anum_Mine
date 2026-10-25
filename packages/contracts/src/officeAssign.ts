// Assign work from the headquarters and add a building from the assistant (docs/ANUM_MINE_ASSIGN_WORK.md,
// docs/ANUM_MINE_EXECUTIVE_PA.md). Wire shapes are snake_case, as the API sends them.

export type AssignPriority = 'low' | 'medium' | 'high';
export type AssignSource = 'agent_card' | 'desk' | 'room' | 'team_panel' | 'flow_dock' | 'executive_pa' | 'command' | 'other';

/** `POST /api/v1/office/assignments` (header `Idempotency-Key` is required). */
export interface AssignRequest {
  agent_id: string;
  title: string;
  instructions: string;
  expected_result?: string | null;
  context_note?: string | null;
  priority: AssignPriority;
  due_at?: string | null;
  run: boolean;
  source: AssignSource;
}

export interface AssignResponse {
  task: { id: string; title: string; status: string; agent_id: string | null; priority?: AssignPriority; due_at?: string | null; created_at: string };
  replayed: boolean;
  run: { id: string; status: string } | null;
  approval: { id: string; action: string; risk_level: string } | null;
  /** The task exists but the run did not start (for example the monthly budget is used up). */
  run_error: { status_code: number; detail: string } | null;
}

export interface BuildingPlanRoom { id: string; template_id: string; name: string; name_ar: string; kind: string; capacity: number; x: number; z: number; width: number; depth: number }
export interface BuildingPlanFloor { id: string; level: number; name: string; width_m: number; depth_m: number; rooms: BuildingPlanRoom[] }

/** `GET /api/v1/office/building-proposals?name=&floors=&lang=` and the assistant tool `propose_add_building`. Creates nothing. */
export interface BuildingProposal {
  version: 1;
  content: {
    lang: 'en' | 'ar';
    building: { id: string; name: string; timezone: string };
    floors: BuildingPlanFloor[];
    totals: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  };
  payload_hash: string;
  bounds: { width_m: number; depth_m: number };
  creates: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  can_confirm: boolean;
  governed_by: string;
  note: string;
}

/** `POST /api/v1/office/building-proposals/confirm`: only a click confirms. */
export interface BuildingConfirm { name: string; floors: number; lang: 'en' | 'ar'; payload_hash: string; input_mode: 'click' }
export interface BuildingConfirmResult {
  version: 1; replayed: boolean; building: { id: string; name: string };
  floor_ids?: string[]; room_ids?: string[]; creates?: BuildingProposal['creates'];
}
