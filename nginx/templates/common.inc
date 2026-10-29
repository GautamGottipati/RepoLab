# Shared by the HTTP-only and HTTPS server blocks. Included, not templated.
#
# The API's routes have no /api prefix; nginx strips it, so the browser talks to
# one origin and no backend route changes.

client_max_body_size 20m;

# Server-Sent Events: GET /jobs/{id}/events and /experiments/{id}/events.
# Never buffered, held open for an hour; the API sends a comment every 15 s
# while idle, so a quiet job does not trip the read timeout.
location ~ ^/api/(jobs|experiments)/[^/]+/events$ {
    rewrite ^/api/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

# The cqlsh terminal WebSocket: the only route that upgrades.
location ~ ^/api/cli/sessions/[^/]+/terminal$ {
    rewrite ^/api/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    # A terminal waits up to 420 s for its node to start, then idles.
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
}

location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
