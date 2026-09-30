// Seeded bug flags. Selected with the SEEDED_BUGS env var:
//   SEEDED_BUGS=all (default) | none | BUG-001,BUG-003
// The ids and their expected detection live in benchmark/expected-results.yaml.

export const BUG_IDS = ["BUG-001", "BUG-002", "BUG-003", "BUG-004"] as const;
export type BugId = (typeof BUG_IDS)[number];

export function parseSeededBugs(raw: string | undefined): BugId[] {
  const value = (raw ?? "all").trim();
  if (value === "" || value.toLowerCase() === "all") return [...BUG_IDS];
  if (value.toLowerCase() === "none") return [];

  const ids = value
    .split(",")
    .map((id) => id.trim().toUpperCase())
    .filter(Boolean);
  const unknown = ids.filter((id) => !BUG_IDS.includes(id as BugId));
  if (unknown.length > 0) {
    throw new Error(
      `Unknown SEEDED_BUGS entries: ${unknown.join(", ")}. ` +
        `Valid values: all, none, or a comma list of ${BUG_IDS.join(", ")}`,
    );
  }
  return [...new Set(ids)] as BugId[];
}
