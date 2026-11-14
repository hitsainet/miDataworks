// The price per call of an endpoint role (017 TD-017.44), as the operator reads and enters it.
// The backend stores US dollars with up to six decimals (Numeric(18, 6)) and parses the value it
// is sent as a decimal, so the field is sent as the text typed, never through a float.

/** A stored price as plain decimal text: 0.000015, never 1.5e-5. */
export function priceText(price: number): string {
  const fixed = price.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  return fixed === '' ? '0' : fixed;
}

/** What a role save sends for the price. Empty clears a stored price; the stored price unchanged
 * is left to the server (null keeps it, and a move to another server clears it there, because a
 * price belongs to the server it was entered for); anything else is sent as typed. */
export function priceWrite(
  stored: number | null | undefined,
  entered: string,
): { price_per_call_usd: string | null; clear_price: boolean } {
  const text = entered.trim();
  const storedText = stored === null || stored === undefined ? '' : priceText(stored);
  if (text === '') return { price_per_call_usd: null, clear_price: storedText !== '' };
  if (text === storedText) return { price_per_call_usd: null, clear_price: false };
  return { price_per_call_usd: text, clear_price: false };
}
