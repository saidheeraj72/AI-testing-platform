import type { Criterion } from "../api/types";

/** Python's repr() of a string, so texts match the backend's check results exactly. */
function pyRepr(s: string | null): string {
  const v = s ?? "None";
  if (s === null) return v;
  if (v.includes("'") && !v.includes('"')) return `"${v}"`;
  return `'${v.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

/** Same text as Criterion.describe() in backend/app/schemas/plan.py (check results are keyed by it). */
export function describeCriterion(c: Criterion): string {
  const neg = c.negate ? "NOT " : "";
  switch (c.type) {
    case "url_contains":
      return `URL ${neg}contains ${pyRepr(c.value)}`;
    case "text_visible":
      return `text ${pyRepr(c.value)} is ${neg}visible${c.within ? ` within ${pyRepr(c.within)}` : ""}`;
    case "element_present":
      return `${c.role || "element"}${c.name ? ` ${pyRepr(c.name)}` : ""} is ${neg}present`;
    case "field_value":
      return `field ${pyRepr(c.name)} value is ${neg}${pyRepr(c.value)}`;
    case "request_succeeded":
      return `${c.method || "any"} request to ${pyRepr(c.value)} succeeded`;
    case "sum_equals":
      return "the listed amounts add up to the total (give parts and total when you verify)";
    default:
      return c.type;
  }
}
