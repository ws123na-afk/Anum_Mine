// Purchases, deliveries, returns and refunds (docs/purchases.md#api), mirror of services/api/anum_api/purchases.
// A purchase never posts money: its payment points at a canonical Finance transaction through a finance link
// (`payment` is a read copy of that link), and a refund points at the refund's own canonical transaction, linked to
// the return of the original purchase. A refund is never income.

export type PurchaseOwnership = 'personal' | 'business';
export type PurchaseStatus =
  | 'ordered' | 'confirmed' | 'shipped' | 'out_for_delivery' | 'delivered' | 'partially_delivered' | 'cancelled' | 'return_requested'
  | 'return_approved' | 'pickup_scheduled' | 'returned' | 'refund_pending' | 'partially_refunded' | 'refunded' | 'disputed';
export type PurchaseReturnStatus =
  | 'requested' | 'approved' | 'pickup_scheduled' | 'returned' | 'refund_pending' | 'partially_refunded' | 'refunded' | 'rejected' | 'disputed' | 'cancelled';
export type PurchaseReturnReason = 'damaged' | 'defective' | 'wrong_item' | 'not_as_described' | 'missing_parts' | 'late' | 'changed_mind' | 'other';
export type PurchaseShipmentStatus = 'pending' | 'in_transit' | 'out_for_delivery' | 'delivered' | 'failed' | 'returned_to_sender';
export type PurchasePaymentMethod = 'card' | 'apple_pay' | 'cash' | 'bank_transfer' | 'cash_on_delivery' | 'instalments' | 'gift_card' | 'other';
export type PurchaseEventKind = 'status' | 'note' | 'shipment' | 'return' | 'payment' | 'refund' | 'share';
/** `proof_needed` (a return sent back without proof in the vault) is on the page's task queue only, never a notification. */
export type PurchaseDueKind = 'delivery_expected' | 'delivery_late' | 'return_window' | 'pickup_due' | 'refund_overdue' | 'warranty' | 'missing_confirmation' | 'proof_needed';
/** The list filter: one status, or a group. */
export type PurchaseFilter = PurchaseStatus | 'awaiting' | 'returns' | 'closed' | 'open';

export const PURCHASE_STATUSES: PurchaseStatus[] = [
  'ordered', 'confirmed', 'shipped', 'out_for_delivery', 'delivered', 'partially_delivered', 'cancelled', 'return_requested', 'return_approved',
  'pickup_scheduled', 'returned', 'refund_pending', 'partially_refunded', 'refunded', 'disputed',
];
export const PURCHASE_RETURN_REASONS: PurchaseReturnReason[] = [
  'damaged', 'defective', 'wrong_item', 'not_as_described', 'missing_parts', 'late', 'changed_mind', 'other',
];
export const PURCHASE_SHIPMENT_STATUSES: PurchaseShipmentStatus[] = ['pending', 'in_transit', 'out_for_delivery', 'delivered', 'failed', 'returned_to_sender'];
export const PURCHASE_PAYMENT_METHODS: PurchasePaymentMethod[] = [
  'card', 'apple_pay', 'cash', 'bank_transfer', 'cash_on_delivery', 'instalments', 'gift_card', 'other',
];

/** A read copy of the finance link behind a payment or a refund. Another member's shows no ledger ids. */
export interface PurchaseMoneyLink {
  link_id: string | null;
  canonical_id: string | null;
  status: 'active' | 'needs_review' | 'void';
  status_reason: string | null;
  amount: string;
  currency: string;
  paid_by: string;
  recorded_as_cash: boolean;
  linked_at: string;
  /** A refund only: the purchase's own payment when the refund was linked. */
  original_canonical_id: string | null;
}

export interface PurchaseMoney {
  currency: string;
  amount: string;
}

export interface PurchaseDueItem {
  purchase_id: string;
  merchant: string;
  order_ref: string | null;
  kind: PurchaseDueKind;
  source_id: string;
  due_on: string;
  days_left: number;
  return_id?: string;
}

export interface PurchaseItem {
  id: string;
  owner_user_id: string;
  purchase_id: string;
  position: number;
  description: string;
  sku: string | null;
  quantity: string;
  unit_price: string | null;
  warranty_until: string | null;
  created_at: string;
}

export interface PurchaseShipment {
  id: string;
  owner_user_id: string;
  purchase_id: string;
  carrier: string | null;
  tracking_number: string | null;
  status: PurchaseShipmentStatus;
  shipped_on: string | null;
  expected_on: string | null;
  delivered_on: string | null;
  note: string | null;
  version: number;
  can_edit: boolean;
  /** The carrier and its public tracking link, detected on the server (docs/purchases.md#courier-tracking). */
  tracking?: PurchaseTracking;
}

/** One carrier a tracking number may belong to, with its public tracking page. */
export interface PurchaseCarrierLink {
  carrier: string;
  carrier_name: string;
  url: string;
  confidence: 'certain' | 'likely' | 'possible';
}

export interface PurchaseTracking {
  number: string | null;
  carrier: string | null;
  carrier_name: string | null;
  url: string | null;
  detected_from: 'name' | 'number' | null;
  /** Every carrier the number fits; several when the format is shared (ten digits: DHL Express and Aramex). */
  candidates: PurchaseCarrierLink[];
  /** The configured status provider, or "off" (links only). */
  status_provider: string;
}

export interface PurchaseTrackingStatus {
  state: 'in_transit' | 'out_for_delivery' | 'delivered' | 'exception' | 'unknown';
  description: string;
  checked_at: string;
  events: { at: string | null; description: string; location: string | null }[];
}

/** `GET /api/v1/purchases/shipments/{id}/tracking`. */
export interface PurchaseTrackingView {
  shipment_id: string;
  tracking: PurchaseTracking;
  status: PurchaseTrackingStatus | null;
  status_error: string | null;
}

export interface PurchaseReturnLine {
  item_id: string;
  quantity: string;
}

export interface PurchaseReturn {
  id: string;
  owner_user_id: string;
  purchase_id: string;
  merchant?: string;
  order_ref?: string | null;
  status: PurchaseReturnStatus;
  requested_on: string;
  reason: PurchaseReturnReason;
  reason_note: string | null;
  items: PurchaseReturnLine[];
  amount: string | null;
  currency: string;
  deadline_on: string | null;
  pickup_on: string | null;
  courier: string | null;
  tracking_number: string | null;
  proof_document_id: string | null;
  confirmation_ref: string | null;
  refund_ref: string | null;
  returned_on: string | null;
  expected_refund_on: string | null;
  refunded_on: string | null;
  dispute_note: string | null;
  refunds: PurchaseMoneyLink[];
  /** Refunded in the return's own currency. */
  refunded: string;
  /** Refunded per currency, never added across currencies. */
  refunded_all: PurchaseMoney[];
  version: number;
  can_edit: boolean;
  can_refund: boolean;
  allowed_moves: PurchaseReturnStatus[];
  replayed?: boolean;
}

export interface PurchaseEvent {
  id: string;
  owner_user_id: string;
  purchase_id: string;
  kind: PurchaseEventKind;
  from_status: PurchaseStatus | null;
  to_status: PurchaseStatus | null;
  subject_id: string | null;
  note: string | null;
  source: 'web' | 'iphone' | 'system';
  occurred_at: string;
  created_at: string;
}

export interface Purchase {
  id: string;
  owner_user_id: string;
  ownership: PurchaseOwnership;
  order_ref: string | null;
  merchant: string;
  ordered_on: string;
  currency: string;
  tax_amount: string | null;
  shipping_amount: string | null;
  /** The shop's figure, a detail: money is the linked payment. */
  total: string | null;
  payment_method: PurchasePaymentMethod | null;
  payment: PurchaseMoneyLink | null;
  document_id: string | null;
  expected_delivery_on: string | null;
  delivered_on: string | null;
  /** A label such as "Home", never the address itself. */
  address_ref: string | null;
  warranty_until: string | null;
  return_window_until: string | null;
  status: PurchaseStatus;
  source: 'web' | 'iphone';
  needs_review: boolean;
  notes: string | null;
  archived_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  can_pay: boolean;
  mine: boolean;
  allowed_moves: PurchaseStatus[];
  due: PurchaseDueItem[];
  refunded: PurchaseMoney[];
  items_total?: string | null;
  /** List rows only: the first item's description, how many items, and the newest shipment. */
  first_item?: string | null;
  item_count?: number;
  latest_shipment?: PurchaseShipment | null;
}

/** The list's extra filters (`GET /api/v1/purchases`). */
export interface PurchaseListFilters {
  q?: string;
  status?: PurchaseFilter | '';
  archived?: boolean;
  merchant?: string;
  /** Order dates, YYYY-MM-DD, inclusive. */
  from?: string | null;
  to?: string | null;
  /** A payment method, or `linked` / `unlinked`. */
  payment?: PurchasePaymentMethod | 'linked' | 'unlinked' | '';
  source?: 'web' | 'iphone' | '';
  /** Under warranty today (the purchase's or an item's). */
  warranty?: boolean;
}

/** `POST /api/v1/purchases/device/share` answer (the iPhone, or Import order on the web). */
/** Save as purchase from the Email Center (docs/purchases.md#save-as-purchase-from-the-email-center). */
export interface PurchaseFromMailInput {
  account_id: string; folder: string; uid: number;
  /** The message attachment to keep as the purchase's receipt (its index), or none. */
  attachment_index?: number | null;
  ownership?: 'personal' | 'business';
}
export interface PurchaseFromMailResult {
  status: 'created' | 'updated' | 'replayed';
  purchase_id: string;
  message: string;
  /** The private vault document the attachment became, when it was kept. */
  document_id?: string | null;
  attachment_kept?: boolean;
}

export interface PurchaseShareResult {
  status: 'created' | 'updated' | 'replayed' | 'test_ok';
  purchase_id?: string;
  purchase_status?: string;
  message: string;
}

export interface PurchaseDetail extends Purchase {
  items: PurchaseItem[];
  shipments: PurchaseShipment[];
  returns: PurchaseReturn[];
  events: PurchaseEvent[];
  events_total: number;
}

export interface PurchasePage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PurchaseItemInput {
  id?: string | null;
  description: string;
  sku?: string | null;
  quantity?: string;
  unit_price?: string | null;
  warranty_until?: string | null;
}

export interface PurchaseInput {
  ownership?: PurchaseOwnership;
  merchant: string;
  order_ref?: string | null;
  ordered_on?: string | null;
  currency?: string | null;
  tax_amount?: string | null;
  shipping_amount?: string | null;
  total?: string | null;
  payment_method?: PurchasePaymentMethod | null;
  document_id?: string | null;
  expected_delivery_on?: string | null;
  address_ref?: string | null;
  warranty_until?: string | null;
  return_window_until?: string | null;
  notes?: string | null;
  status?: 'ordered' | 'confirmed' | 'shipped' | 'out_for_delivery' | 'delivered';
  items?: PurchaseItemInput[];
}

export type PurchasePatch = Partial<Omit<PurchaseInput, 'ownership' | 'status'>> & {
  version: number;
  delivered_on?: string | null;
  needs_review?: boolean;
};

export interface PurchaseMoveInput {
  to: string;
  version: number;
  note?: string | null;
  occurred_at?: string | null;
}

export interface PurchaseShipmentInput {
  carrier?: string | null;
  tracking_number?: string | null;
  status?: PurchaseShipmentStatus;
  shipped_on?: string | null;
  expected_on?: string | null;
  delivered_on?: string | null;
  note?: string | null;
}

export interface PurchaseReturnInput {
  requested_on?: string | null;
  reason: PurchaseReturnReason;
  reason_note?: string | null;
  items?: { item_id: string; quantity: string }[];
  amount?: string | null;
  deadline_on?: string | null;
  pickup_on?: string | null;
  courier?: string | null;
  tracking_number?: string | null;
  proof_document_id?: string | null;
  confirmation_ref?: string | null;
  expected_refund_on?: string | null;
}

export type PurchaseReturnPatch = Partial<Omit<PurchaseReturnInput, 'requested_on'>> & {
  version: number;
  refund_ref?: string | null;
  dispute_note?: string | null;
};

export interface PurchaseOverview {
  purchases: number;
  awaiting_delivery: number;
  late: number;
  open_returns: number;
  needs_review: number;
  unlinked: number;
  spend: PurchaseMoney[];
  refunded: PurchaseMoney[];
  net: PurchaseMoney[];
  refunds_owed: PurchaseMoney[];
  monthly: { month: string; currency: string; amount: string }[];
  due: PurchaseDueItem[];
  /** This month (YYYY-MM, UTC): orders placed (not cancelled) and the shops' totals per currency, a detail, not the ledger. */
  month: string;
  orders_this_month: number;
  committed_this_month: PurchaseMoney[];
  untotalled_this_month: number;
  /** Shipments in transit or out for delivery. */
  in_transit: number;
}

export interface PurchasePreferences {
  owner_user_id?: string;
  reminders: boolean;
  lead_days: number;
  warranty_days: number;
  default_currency: string;
  version: number;
  updated_at?: string | null;
}

export interface PurchasePaymentCandidate {
  canonical_id: string;
  posted_on: string;
  merchant: string;
  category: string | null;
  amount: string;
  currency: string;
  remaining: string;
  linked_to_purchase: boolean;
}

export interface PurchaseLinkInput {
  canonical_id: string;
  amount?: string | null;
  currency?: string | null;
}

export interface PurchaseCashInput {
  account_id: string;
  amount: string;
  posted_on: string;
}

export interface PurchasePaymentResult {
  payment: PurchaseMoneyLink;
  replayed: boolean;
}

export interface PurchaseRefundResult {
  refund: PurchaseMoneyLink;
  replayed: boolean;
}

/** The iPhone Shortcut body for a shared order confirmation or delivery message. */
export interface PurchaseShareEvent {
  action?: 'save' | 'test';
  text?: string | null;
  occurred_at?: string | null;
  idempotency_key?: string | null;
  ownership?: PurchaseOwnership;
}
