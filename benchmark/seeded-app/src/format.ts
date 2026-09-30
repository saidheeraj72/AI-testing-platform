import type { CartLine } from "./types";
import { hasBug } from "./bugs";

export function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export function cartTotal(items: CartLine[]): number {
  if (hasBug("BUG-002")) {
    // Seeded BUG-002: total ignores quantity.
    return items.reduce((sum, item) => sum + item.price, 0);
  }
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
