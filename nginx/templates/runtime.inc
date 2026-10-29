# Live web apps: rt-<runtime>-<service>.<domain>. The API's RuntimeProxyMiddleware
# recognises the Host and relays (HTTP and WebSocket) only to a running app's
# sidecar; any other host it refuses. Needs wildcard DNS, and for HTTPS a
# wildcard certificate. Without them this server block is simply never hit.
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 3600s;
client_max_body_size 20m;
location / {
    proxy_pass http://127.0.0.1:8000;
}
