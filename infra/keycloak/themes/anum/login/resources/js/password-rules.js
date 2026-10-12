// Live password rules for Keycloak's sign-up and change-password pages (ANUM login theme).
//
// password-validation.ftl renders the realm's password policy as `rules` and calls
// attachPasswordRules(). The checks mirror Keycloak's server-side policy providers
// (length and maxLength count UTF-16 code units like Java's String.length(); upper and
// lower case, digits and "special" characters use the same Unicode categories as
// Character.isUpperCase / isLowerCase / isDigit / !isLetterOrDigit). The server still
// enforces the policy on submit: this is guidance, never the check.
//
// evaluatePassword() has no DOM dependency so apps/web/test/keycloakTheme.test.ts can run it.

const count = (value, pattern) => (value.match(pattern) || []).length;

const checks = {
  length: (rule, value) => value.length >= rule.value,
  maxLength: (rule, value) => value.length <= rule.value,
  lowerCase: (rule, value) => count(value, /\p{Ll}/gu) >= rule.value,
  upperCase: (rule, value) => count(value, /\p{Lu}/gu) >= rule.value,
  digits: (rule, value) => count(value, /\p{Nd}/gu) >= rule.value,
  specialChars: (rule, value) => count(value, /[^\p{L}\p{Nd}]/gu) >= rule.value,
  // An empty password trivially differs from the username: show it unmet until typed.
  notUsername: (_rule, value, context) => value.length > 0 && (!context.username || value.toLowerCase() !== context.username.toLowerCase()),
  notEmail: (_rule, value, context) => value.length > 0 && (!context.email || value.toLowerCase() !== context.email.toLowerCase()),
};

/** Which of `rules` the password meets. Unknown rule names are reported as met (the server decides). */
export function evaluatePassword(value, rules, context = {}) {
  return rules.map((rule) => ({ name: rule.name, met: checks[rule.name] ? checks[rule.name](rule, value, context) : true }));
}

/**
 * Inserts the rule list under the password field `fieldId` and keeps it current. The list is
 * the field's description (aria-describedby); each item carries a visually hidden "met" or
 * "not met" so screen readers hear the state, not only the colour.
 */
export function attachPasswordRules(doc, fieldId, rules, text) {
  const input = doc.getElementById(fieldId);
  if (!input || rules.length === 0) return null;
  const list = doc.createElement('ul');
  list.id = `anum-password-rules-${fieldId}`;
  list.className = 'anum-password-rules';
  list.setAttribute('aria-label', text.heading);
  const items = rules.map((rule) => {
    const item = doc.createElement('li');
    item.dataset.rule = rule.name;
    const state = doc.createElement('span');
    state.className = 'anum-visually-hidden';
    const label = doc.createElement('span');
    label.textContent = rule.label;
    item.append(state, label);
    list.append(item);
    return { item, state };
  });
  const anchor = doc.getElementById(`input-error-container-${fieldId}`);
  if (anchor?.parentNode) anchor.parentNode.insertBefore(list, anchor);
  else input.insertAdjacentElement('afterend', list);
  const described = input.getAttribute('aria-describedby');
  input.setAttribute('aria-describedby', described ? `${described} ${list.id}` : list.id);

  const update = () => {
    const context = {
      username: doc.getElementById('username')?.value || doc.getElementById('email')?.value || '',
      email: doc.getElementById('email')?.value || '',
    };
    evaluatePassword(input.value, rules, context).forEach((result, index) => {
      items[index].item.classList.toggle('anum-rule-met', result.met);
      items[index].state.textContent = `${result.met ? text.met : text.unmet}: `;
    });
  };
  input.addEventListener('input', update);
  doc.getElementById('username')?.addEventListener('input', update);
  doc.getElementById('email')?.addEventListener('input', update);
  update();
  return list;
}
