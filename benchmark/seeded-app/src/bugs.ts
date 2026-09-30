export function hasBug(id: "BUG-002" | "BUG-003" | "BUG-004"): boolean {
  return __SEEDED_BUGS__.includes(id);
}
