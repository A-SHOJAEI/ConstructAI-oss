# Offline Mode Design Document

## Problem

Construction sites frequently have intermittent or no internet connectivity. Currently, all ConstructAI features require API connectivity — no local caching, no offline queue, no service worker. Users lose access to critical project data when connectivity drops.

## Goals

1. Read access to critical data (project list, RFIs, punch list, daily logs) when offline
2. Ability to create/update records offline that sync when connectivity returns
3. Clear visual indicator of offline status and pending sync items
4. Conflict resolution for concurrent edits

## Architecture

### Service Worker (Workbox)

Register a service worker using Workbox via `next-pwa` or manual registration in `apps/web/`:

- **Static assets**: Cache-first (CSS, JS, fonts, images)
- **API data (GET)**: StaleWhileRevalidate for frequently accessed endpoints
- **API mutations (POST/PUT/DELETE)**: BackgroundSync queue

### Cached API Endpoints

| Endpoint | Strategy | TTL | Priority |
|----------|----------|-----|----------|
| `/api/v1/projects/` | StaleWhileRevalidate | 5 min | P0 |
| `/api/v1/projects/{id}/rfis` | StaleWhileRevalidate | 5 min | P0 |
| `/api/v1/projects/{id}/punch-list` | StaleWhileRevalidate | 5 min | P0 |
| `/api/v1/projects/{id}/daily-logs` | StaleWhileRevalidate | 10 min | P1 |
| `/api/v1/projects/{id}/safety/alerts` | NetworkFirst | 1 min | P1 |
| `/api/v1/projects/{id}/documents` | StaleWhileRevalidate | 15 min | P2 |

### IndexedDB Schema

Store full API responses in IndexedDB (via `idb` library) for offline reads:

```
constructai_offline_db/
├── projects/          # Project list + details
├── rfis/              # RFI list per project
├── punch_items/       # Punch list per project
├── daily_logs/        # Daily logs per project
└── pending_mutations/ # Queue of offline POST/PUT/DELETE
```

### Mutation Queue

When offline, mutations are stored in `pending_mutations`:

```typescript
interface PendingMutation {
  id: string;           // UUID
  timestamp: number;    // Date.now()
  method: 'POST' | 'PUT' | 'DELETE';
  url: string;
  body: unknown;
  retryCount: number;
}
```

On reconnect, the service worker processes the queue in FIFO order. If a mutation fails (409 Conflict), it's flagged for manual resolution.

### Conflict Resolution

- **Last-write-wins** for simple field updates (punch list status, RFI priority)
- **Manual merge** for rich text fields (daily log notes, RFI responses)
- **Reject** for structural conflicts (deleting an item that was modified)

Display a conflict resolution UI showing local vs. server state with merge options.

### UI Indicators

- **Offline banner**: Fixed top bar showing "You're offline — changes will sync when connected"
- **Pending badge**: Count of queued mutations in the sidebar
- **Sync status**: Per-item indicator (synced / pending / conflict)

## Implementation Plan

1. **Phase A** (1 week): Service worker setup, static asset caching, offline detection
2. **Phase B** (1 week): IndexedDB storage, GET request caching, stale data display
3. **Phase C** (1 week): Mutation queue, background sync, conflict detection
4. **Phase D** (3 days): Conflict resolution UI, sync status indicators, testing

## Dependencies

- `workbox-webpack-plugin` or `next-pwa`
- `idb` (IndexedDB wrapper)
- `uuid` (already in project)

## Risks

- IndexedDB storage limits (~50MB in Safari, unlimited in Chrome with permission)
- Service worker cache invalidation complexity
- Conflict resolution UX needs user testing
