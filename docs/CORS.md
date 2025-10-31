# CORS Configuration

This document describes how to configure Cross-Origin Resource Sharing (CORS) for the Codeless Orchestration Engine.

## Overview

CORS is essential when your front-end application (e.g., Agent Builder Test Panel) runs on a different origin than the orchestration engine. Modern browsers enforce CORS policies and send **preflight OPTIONS requests** when using custom headers or non-simple HTTP methods.

The engine supports configurable CORS middleware to handle preflight requests and allow cross-origin access from trusted origins.

## Configuration

All CORS settings are controlled via environment variables in your `.env` file:

### Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CORS_ENABLED` | `bool` | `true` | Enable/disable CORS middleware |
| `CORS_ALLOW_ORIGINS` | `list[str]` | See below | Comma-separated list of allowed origins |
| `CORS_ALLOW_CREDENTIALS` | `bool` | `true` | Allow cookies and authorization headers |
| `CORS_ALLOW_METHODS` | `list[str]` | `["*"]` | Allowed HTTP methods (or `*` for all) |
| `CORS_ALLOW_HEADERS` | `list[str]` | `["*"]` | Allowed request headers (or `*` for all) |
| `CORS_EXPOSE_HEADERS` | `list[str]` | See below | Headers exposed to the client |

### Default Allowed Origins

By default, the following localhost origins are allowed for development:
- `http://localhost:5173` (Vite default)
- `http://127.0.0.1:5173`
- `http://localhost:3000` (React/Next.js default)
- `http://127.0.0.1:3000`
- `http://localhost:8080` (common dev port)

### Default Exposed Headers

The following headers are exposed to clients by default:
- `X-Run-Id`
- `X-Request-Id`
- `X-Correlation-Id`
- `Content-Type`
- `Cache-Control`

## Development Setup

For local development, the defaults should work out of the box. If your front-end runs on a different port, add it to your `.env`:

```bash
# CORS (development)
CORS_ENABLED=true
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://localhost:4200
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=GET,POST,OPTIONS
CORS_ALLOW_HEADERS=Authorization,Content-Type,Accept,Cache-Control,X-Requested-With,X-Tenant-ID,X-Request-ID,X-Correlation-ID,X-Telemetry,X-Run-Id
CORS_EXPOSE_HEADERS=X-Run-Id,X-Request-Id,X-Correlation-Id,Content-Type,Cache-Control
```

## Production Setup

In production, you should **explicitly list** only your trusted front-end origins:

```bash
# CORS (production)
CORS_ENABLED=true
CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=GET,POST,OPTIONS
CORS_ALLOW_HEADERS=Authorization,Content-Type,Accept,Cache-Control,X-Requested-With,X-Tenant-ID,X-Request-ID,X-Correlation-ID,X-Telemetry,X-Run-Id
CORS_EXPOSE_HEADERS=X-Run-Id,X-Request-Id,X-Correlation-Id,Content-Type,Cache-Control
```

**Security Best Practices:**
- Never use `*` for `CORS_ALLOW_ORIGINS` in production
- Explicitly list only trusted domains
- Use HTTPS origins in production
- Avoid using `*` for headers/methods if you can list them explicitly

## Disabling CORS

If your front-end and engine are served from the same origin, you can disable CORS:

```bash
CORS_ENABLED=false
```

## Streaming Endpoints

The `/v1/execute/stream` endpoint supports:
- **POST** requests with JSON orchestration payloads
- **OPTIONS** preflight requests (automatically handled)

Server-Sent Events (SSE) responses include the required headers:
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`

## Troubleshooting

### 405 Method Not Allowed on OPTIONS

**Symptom:** Browser console shows `405 Method Not Allowed` for OPTIONS requests.

**Solution:** 
- Verify `CORS_ENABLED=true` in your `.env`
- Check that the OPTIONS handler is registered for `/v1/execute/stream`
- Restart the server after changing configuration

### CORS Error: Origin Not Allowed

**Symptom:** Browser console shows "CORS policy: No 'Access-Control-Allow-Origin' header is present"

**Solution:**
- Add your front-end's origin to `CORS_ALLOW_ORIGINS`
- Ensure the origin matches exactly (including protocol and port)
- Example: `http://localhost:5173` ≠ `http://127.0.0.1:5173`

### Credentials Flag Error

**Symptom:** "Credentials mode is 'include' but the Access-Control-Allow-Credentials header is not set"

**Solution:**
- Set `CORS_ALLOW_CREDENTIALS=true`
- Ensure `CORS_ALLOW_ORIGINS` does NOT use `*` when credentials are enabled

### Missing Custom Headers

**Symptom:** Custom headers like `X-Tenant-ID` or `Authorization` are not being sent

**Solution:**
- Add the headers to `CORS_ALLOW_HEADERS`
- For preflight requests, ensure `Access-Control-Request-Headers` matches allowed headers

### Preflight Timeout / 499 Errors

**Symptom:** OPTIONS requests hang or return 499 Client Closed Request

**Solution:**
- Check that your reverse proxy/load balancer passes OPTIONS requests
- Verify no middleware is blocking OPTIONS
- Ensure `max_age` is set appropriately (default: 86400 seconds)

## Testing CORS

### Manual Test with cURL

Test preflight for the streaming endpoint:

```bash
curl -i -X OPTIONS "http://127.0.0.1:8000/v1/execute/stream" \
  -H "Origin: http://localhost:5173" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,x-telemetry,x-tenant-id,x-request-id,x-correlation-id"
```

Expected response:
- Status: `200` or `204`
- Headers include:
  - `Access-Control-Allow-Origin: http://localhost:5173` (or `*`)
  - `Access-Control-Allow-Methods: ...`
  - `Access-Control-Allow-Headers: ...`
  - `Access-Control-Allow-Credentials: true` (if enabled)

### Browser DevTools

1. Open your browser's Developer Tools (F12)
2. Go to the Network tab
3. Look for OPTIONS requests to `/v1/execute/stream`
4. Check the Response Headers for `Access-Control-*` headers
5. Verify no CORS errors in the Console tab

## References

- [MDN: CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)
- [FastAPI CORS Middleware](https://fastapi.tiangolo.com/tutorial/cors/)
- [Preflight Requests](https://developer.mozilla.org/en-US/docs/Glossary/Preflight_request)
