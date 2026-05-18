// L-12: Shared route-parameter validators. Dynamic pages cast param as
// `string` and trust the API to 404 bad values — works but provides no
// early signal. isUuid() lets pages redirect or surface a friendly error
// before the network round-trip.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}
