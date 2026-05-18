# Nginx reverse proxy with sub_filter nonce injection for the
# ConstructAI demo. Owns CSP for the front-end so we don't have to rely
# on Next.js 15's standalone build to inject nonces (which it doesn't
# do reliably for inline hydration scripts).
#
# Using the alpine image because it's small (~50 MB) and has nginx with
# the http_sub_module compiled in.

FROM nginx:1.27-alpine

# Strip default site config — ours fully replaces it.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/nginx.conf

# Health check: ping the proxy's own /healthz endpoint.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/healthz || exit 1

EXPOSE 80
