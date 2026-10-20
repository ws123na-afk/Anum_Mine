// The Accounts tab of Business (docs/business-portal.md#giving-access): small pure helpers.

/** "Riyadh, SA" from whichever of the city and the country the account has. */
export function accountPlace(account: { city: string; country: string }): string {
  return [account.city, account.country].map((part) => part.trim()).filter(Boolean).join(', ');
}
