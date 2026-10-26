/** "Set up my campus" typed or spoken in ANUM Command or the executive dock (spec: docs/ANUM_MINE_STARTER_CAMPUS.md#first-run).
 * The sentence parser only decides whether to SHOW the proposal card. Creating is the card's button, a click that sends the plan hash
 * the person saw; no spoken or typed word ever reaches the confirm call. */

const EN = /^(?:please\s+)?(?:can you\s+|could you\s+)?(?:set\s*up|setup|create|build|make|start|generate|add)\s+(?:me\s+)?(?:my|the|our|a|an)\s+(?:starter\s+|standard\s+|whole\s+|full\s+|new\s+)?(?:campus|offices|office\s+campus)(?![\p{L}\p{N}])\s*(?:please|now|for me)?[.!?]*$/iu;
const AR = /^(?:من\s*فضلك\s+)?(?:أنشئ|انشئ|أنشيء|جهّز|جهز|أعدّ|اعد|أعد|ابن|ابنِ|إعداد|اعداد)\s+(?:لي\s+)?(?:الحرم|حرمي|الحرم\s+الخاص\s+بي|الحرم\s+القياسي)(?![\p{L}\p{N}])\s*(?:من\s*فضلك|الآن)?[.!؟?]*$/u;

/** Is this sentence a request to set up the starter campus? Any other sentence (including "add a building") returns false. */
export function parseCampusRequest(text: string): boolean {
  const s = text.replace(/\s+/g, ' ').trim();
  if (!s || s.length > 120) return false;
  return EN.test(s) || AR.test(s);
}
