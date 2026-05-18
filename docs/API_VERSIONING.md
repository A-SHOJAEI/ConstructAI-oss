# API Versioning Guide

## Current Version

The ConstructAI API is currently at **v1**. All endpoints are prefixed with `/api/v1/`.

## Versioning Strategy

ConstructAI uses **URL path versioning** with the following principles:

### Breaking Changes (Major Version Bump)
- Removing an endpoint
- Removing a required field from a response
- Changing the type of an existing field
- Changing error response formats
- Changing authentication mechanisms

### Non-Breaking Changes (No Version Bump)
- Adding new endpoints
- Adding optional fields to requests
- Adding new fields to responses
- Adding new enum values
- Performance improvements
- Bug fixes that don't change the API contract

## Deprecation Policy

1. **Announcement**: Deprecated endpoints are marked with a `Deprecated` header in the response and documented in the changelog.
2. **Grace Period**: Deprecated v(N) endpoints remain available for 6 months after v(N+1) is released.
3. **Sunset**: After the grace period, deprecated endpoints return `410 Gone`.

## Migration Guide

When a new API version is released:

1. Check the [CHANGELOG.md](../CHANGELOG.md) for breaking changes
2. Update your client to use the new version prefix
3. Test all integrations against the new version
4. Update any webhook URLs to use the new prefix

## Content Negotiation

All API responses use `application/json` by default. Specific endpoints support additional content types:

| Content Type | Endpoints |
|---|---|
| `application/json` | All endpoints (default) |
| `text/csv` | `/api/v1/projects/{id}/exports/*` |
| `application/pdf` | Report generation endpoints (future) |

## Rate Limiting

Rate limits are applied per user and communicated via response headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1709251200
```

Auth-specific endpoints (`/auth/login`, `/auth/register`) have stricter limits (10/minute).

## Error Format

All API errors follow a consistent format:

```json
{
  "detail": "Human-readable error message",
  "status_code": 400,
  "error_code": "VALIDATION_ERROR"
}
```

Validation errors include field-level details:

```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

## Authentication

### Bearer Token
```
Authorization: Bearer <access_token>
```

### Cookie-Based (Browser)
Cookies are set automatically on login. Include `credentials: "include"` in fetch requests.

### CSRF Protection
For cookie-based auth, mutation requests (POST, PUT, PATCH, DELETE) require the `X-CSRF-Token` header matching the `csrf_token` cookie value.
