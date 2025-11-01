# Development Proxy Support

The orchestration engine can route outbound HTTP requests to the Apigee/OpenAI gateway (and its OAuth token endpoint) through a local debugging proxy such as [Fiddler Classic](https://www.telerik.com/fiddler) or [Fiddler Everywhere](https://www.telerik.com/fiddler-everywhere). This is useful when you need to inspect or replay gateway traffic while developing locally.

## Configuration

Add the following settings to your `.env.local` file (or export the environment variables directly) to enable proxying:

```ini
PROXY_ENABLED=true
PROXY_URL=http://127.0.0.1:8888
PROXY_CA_BUNDLE=-----BEGIN CERTIFICATE-----
...
-----END CERTIFICATE-----
```

- `PROXY_ENABLED`: Turns proxying on or off without changing other settings.
- `PROXY_URL`: The HTTP/HTTPS address of your local proxy. Fiddler defaults to `http://127.0.0.1:8888`.
- `PROXY_CA_BUNDLE`: The proxy's root certificate so HTTPS requests succeed when the proxy MITMs TLS. The bundle can be the raw PEM text (as shown) or the Base64-encoded DER blob; the engine normalizes either format.

When the proxy bundle is omitted, the runtime falls back to the system trust store (`verify=True`). When present, the bundle is loaded into an `ssl.SSLContext` that is shared by the token and gateway clients.

## httpx compatibility

httpx 0.28 removed the long-standing `proxies=` keyword in favour of `proxy=` (or proxy-aware transports). The engine inspects the available parameters on `httpx.AsyncClient` at runtime and automatically chooses the correct wiring, falling back to an `AsyncHTTPTransport(proxy=...)` when neither keyword is available. No additional configuration is required when upgrading httpx.

## Exporting the Fiddler certificate

1. Open Fiddler and ensure HTTPS decryption is enabled.
2. Choose **Tools → Options → HTTPS → Actions → Export Root Certificate to Desktop**.
3. Open the exported certificate:
   - For a PEM file (`.cer` viewed in Notepad), copy the entire block including `-----BEGIN CERTIFICATE-----` / `-----END CERTIFICATE-----` into `PROXY_CA_BUNDLE`.
   - For DER/Base64 output, run `certutil -encode export.cer export.b64` (Windows) or `openssl base64 -in export.cer` (macOS/Linux) and paste the resulting Base64 string (without newlines) into `PROXY_CA_BUNDLE`.

> Tip: wrap long Base64 strings to avoid accidentally committing secrets to version control. The settings loader re-wraps the data into 64-character lines when converting to PEM.

## Troubleshooting

- **HTTP 407 Proxy Authentication Required**: Fiddler may require authentication when running with alternate credentials. Clear the `PROXY_*` variables or configure the proxy for anonymous access.
- **SSL verification errors**: Verify that `PROXY_CA_BUNDLE` matches the certificate that Fiddler is actively using. Regenerate and paste the bundle again if Fiddler refreshed its trust root.
- **No traffic captured**: Confirm `PROXY_ENABLED=true` and that the proxy is listening on the configured port. Restart the API process after changing proxy settings so the HTTP clients pick up the new configuration.
- **Requests bypass the proxy**: Ensure `PROXY_URL` is reachable (e.g., `curl -x http://127.0.0.1:8888 https://example.com`). Some corporate VPNs can block loopback proxy traffic; try an alternate interface such as `http://localhost:8888`.
