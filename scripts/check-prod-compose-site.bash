# shellcheck shell=bash
# The swag site config: the nginx directive tokenizer, the rules and nginx -t.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# --- The swag site config: nginx's directives, not its lines; then nginx's own syntax check -------
# #500: the first version counted lines, and four spellings of a second `proxy_set_header
# X-Forwarded-For` got past it (a second directive on the line of another, one split over two lines,
# one after a quoted "#" the comment stripper ate, one inside a one-line location). So the file is
# read the way nginx reads it (site_directives, a tokenizer for ngx_conf_read_token's rules) and the
# rules (site_verdict) look at directives and the block each one is in. Why not `nginx -T`: it dumps
# the files as written, comments and layout intact, so it normalizes nothing; and the tokenizer needs
# no Docker, which the owner's pre-deploy run may not reach. `nginx -t` still runs on the same file.
# This is a best-effort lint of honest mistakes, like the compose checks (see the header).
site_label="$site"
sfail() { echo "check-prod-compose: $site_label: $*" >&2; return 1; }

# site_directives FILE: one tab-separated record per token run, in file order:
#   D<TAB>ctx<TAB>name<TAB>arg...   a directive (ctx: empty, server, server/location)
#   B<TAB>ctx<TAB>name<TAB>arg...   a block opens (only `server`, then `location` inside it)
#   E<TAB>ctx                       a block closed (ctx: where we are now)
#   ERR<TAB>message                 not readable (exit 1)
# Tokens as nginx reads them: whitespace separates; `#` starts a comment only at the start of a token;
# '...' and "..." quote (so `;` `{` `#` inside are text) and must be followed by a delimiter; a
# backslash makes the next character plain; `${name}` is `$name` and its `{` opens no block; `}`
# inside a token is text; `;` ends a directive, `{` opens a block, `}` closes it. Tokens are unquoted
# and their tabs and newlines become spaces.
# shellcheck disable=SC2016  # awk's $ variables, not shell ones
site_directives() {
  timeout 30 awk '
    function err(m,   t, ln) { t = substr(s, 1, i); ln = gsub(/\n/, "", t) + 1; print "ERR\tline " ln ": " m; failed = 1; exit 1 }
    function emit(kind,   line, k) {
      line = kind "\t" ctx
      for (k = 1; k <= nt; k++) line = line "\t" tk[k]
      print line; nt = 0
    }
    function esc(d) { return (d == "\"" || d == "\047" || d == "\\") ? d : "\\" d }
    { s = s $0 "\n" }
    END {
      n = length(s); i = 1; nt = 0; depth = 0; ctx = ""
      while (i <= n) {
        c = substr(s, i, 1)
        if (c == " " || c == "\t" || c == "\n" || c == "\r") { i++; continue }
        if (c == "#") { while (i <= n && substr(s, i, 1) != "\n") i++; continue }
        if (c == ";") { if (nt == 0) err("unexpected ;"); emit("D"); i++; continue }
        if (c == "{") {
          if (nt == 0) err("unexpected {")
          if (depth == 0 && !(tk[1] == "server" && nt == 1)) err("only a server block is read at the top of this file")
          if (depth == 1 && tk[1] != "location") err("only location blocks are read inside the server")
          if (depth >= 2) err("no block inside a location")
          emit("B"); depth++; ctx = (depth == 1 ? "server" : "server/location"); i++; continue
        }
        if (c == "}") {
          if (nt > 0) err("missing ; before }")
          if (depth == 0) err("unexpected }")
          depth--; ctx = (depth == 0 ? "" : "server"); print "E\t" ctx; i++; continue
        }
        tok = ""
        if (c == "\"" || c == "\047") {
          q = c; i++; closed = 0
          while (i <= n) {
            c = substr(s, i, 1)
            if (c == "\\") { d = substr(s, i + 1, 1); tok = tok esc(d); i += 2; continue }
            if (c == q) { closed = 1; i++; break }
            tok = tok c; i++
          }
          if (!closed) err("unterminated quote")
          if (i <= n && substr(s, i, 1) !~ /[ \t\r\n;{)]/) err("unexpected character after a closing quote")
        } else {
          v = 0
          while (i <= n) {
            c = substr(s, i, 1)
            if (c == "\\") { d = substr(s, i + 1, 1); tok = tok esc(d); i += 2; v = 0; continue }
            if (c == " " || c == "\t" || c == "\n" || c == "\r" || c == ";") break
            if (c == "{") { if (v) { tok = tok c; i++; continue } break }
            v = (c == "$"); tok = tok c; i++
          }
        }
        while (match(tok, /\$\{[A-Za-z0-9_]+\}/)) tok = substr(tok, 1, RSTART - 1) "$" substr(tok, RSTART + 2, RLENGTH - 3) substr(tok, RSTART + RLENGTH)
        gsub(/[\t\r\n]/, " ", tok)
        tk[++nt] = tok
      }
      if (nt > 0) err("missing ; at the end")
      if (depth > 0) err("missing } at the end")
    }
  ' "$1"
}

# site_verdict LIVE: reads site_directives' records; prints each broken rule, exits 1 if any.
# LIVE=1 (the owner's file) lets the Cloudflare include and real_ip_header both be absent (DNS only,
# runbook section 8); the sample must have both.
# shellcheck disable=SC2016  # literal nginx variables, not shell ones
site_verdict() {
  timeout 30 awk -F'\t' -v live="$1" '
    BEGIN { rih = 0; cfinc = 0 }
    function bad(m) { print m; nbad++ }
    function fin() { if (inloc) { if (sse && !buf) bad("the SSE location needs proxy_buffering off"); if (sse && !rt) bad("the SSE location needs proxy_read_timeout of at least 900s") } inloc = 0 }
    { kind = $1; ctx = $2; name = $3; a1 = tolower($4); args = ""
      for (k = 4; k <= NF; k++) args = args (k > 4 ? " " : "") $k }
    kind == "B" {
      if (name == "server") nserver++
      else { fin(); inloc = 1; buf = 0; rt = 0; sse = (index(args, "events") > 0) }
      next
    }
    kind == "E" { fin(); next }
    kind != "D" { next }
    index(args, "proxy_add_x_forwarded_for") { bad("never append to a visitor X-Forwarded-For (no proxy_add_x_forwarded_for)") }
    name == "proxy_set_header" {
      if (ctx == "server/location") bad("a location sets proxy_set_header (it replaces every inherited one, X-Forwarded-For included): set none there")
      if (a1 == "x-forwarded-for") {
        nxff++
        if (ctx != "server" || NF != 5 || $5 != "$remote_addr") badxff = 1
      }
    }
    name == "add_header" {
      if (ctx == "server/location") bad("a location sets add_header (it replaces every inherited one): set none there")
      if (a1 == "content-security-policy" || a1 == "referrer-policy") bad("the app CSP and Referrer-Policy are neither added, hidden nor replaced here")
      if (a1 == "strict-transport-security" && ctx == "server") hsts = 1
    }
    name == "proxy_hide_header" || name == "more_clear_headers" || name == "more_set_headers" { bad("the app CSP and Referrer-Policy are neither added, hidden nor replaced here") }
    name == "include" {
      if (ctx != "server" || NF != 4 || ($4 != "/config/nginx/ssl.conf" && $4 != "/config/nginx/cf_real-ip.conf")) bad("include only /config/nginx/ssl.conf and cf_real-ip.conf, in the server")
      if ($4 == "/config/nginx/cf_real-ip.conf") cfinc = 1
    }
    name == "client_max_body_size" { nbody++; if (ctx != "server" || NF != 4 || $4 != "1m") bad("client_max_body_size 1m in the server only (the app cap is 64 KiB)") }
    name == "server_name" { nsn++; if (ctx != "server" || NF != 4 || $4 !~ /^[a-z0-9.-]+$/) bad("server_name is the exact host") }
    name == "server_tokens" { if (NF != 4 || a1 != "off") bad("no server_tokens on"); else if (ctx == "server") sto = 1 }
    name == "proxy_intercept_errors" && a1 != "off" { bad("the app own errors pass through (no proxy_intercept_errors)") }
    name == "real_ip_header" { if (ctx == "server" && NF == 4 && $4 == "CF-Connecting-IP") rih = 1; else bad("real IP from CF-Connecting-IP (Cloudflare ranges only)") }
    name == "real_ip_recursive" && a1 != "off" { bad("Cloudflare ranges belong in cf_real-ip.conf only, non-recursive") }
    name == "set_real_ip_from" { bad("Cloudflare ranges belong in cf_real-ip.conf only, non-recursive") }
    ctx == "server/location" && name == "proxy_buffering" && a1 == "off" { buf = 1 }
    ctx == "server/location" && name == "proxy_read_timeout" && $4 ~ /^(9[0-9][0-9]|[1-9][0-9][0-9][0-9]+)s$/ { rt = 1 }
    END {
      if (nserver != 1) bad("exactly one server block (found " nserver + 0 ")")
      if (nxff != 1) bad("exactly one proxy_set_header X-Forwarded-For (found " nxff + 0 ")")
      else if (badxff) bad("X-Forwarded-For must be overwritten with $remote_addr, once, in the server and in no location")
      if (nbody != 1) bad("client_max_body_size 1m (found " nbody + 0 " directives)")
      if (nsn != 1) bad("exactly one server_name, the exact host (found " nsn + 0 ")")
      if (!sto) bad("server_tokens off in the server (no nginx version)")
      if (!hsts) bad("add_header Strict-Transport-Security in the server (HSTS is the edge job)")
      if (live ? (rih != cfinc) : !(rih && cfinc)) bad("real IP: include /config/nginx/cf_real-ip.conf and real_ip_header CF-Connecting-IP go together" (live ? " (both or neither with DNS only)" : ""))
      exit (nbad > 0)
    }
  '
}
check_site() {   # check_site FILE [live]
  local dirs msgs l rc=0
  # Both awk programs run under `timeout 30` (exit 124): a loop in them must fail the check, not hang it.
  dirs="$(site_directives "$1")" || rc=$?
  if (( rc == 124 )); then sfail "reading it as nginx directives took more than 30 seconds"; return 1; fi
  if (( rc != 0 )); then
    msgs="${dirs##*$'\n'}"; sfail "cannot read it as nginx directives: ${msgs#ERR$'\t'}"; return 1
  fi
  rc=0
  msgs="$(site_verdict "${2:+1}" <<<"$dirs")" || rc=$?
  if (( rc == 124 )); then sfail "checking its directives took more than 30 seconds"; return 1; fi
  if (( rc != 0 )); then
    while IFS= read -r l; do sfail "$l" || true; done <<<"$msgs"
    return 1
  fi
}

# nginx_log_summary LOG: what a failed `nginx -t` said, without its words (#470, from the review of
# #592). nginx quotes tokens of the file in its messages, and on the owner's live file a token can be
# a secret, so the log itself is never printed: per error line only its level (one of a fixed set)
# and, for the site file, the line number (digits only), at most 5 lines, then a count. Nothing else
# of the log or the file reaches the output.
nginx_log_summary() {   # nginx_log_summary LOG
  awk '
    match($0, /\[(emerg|alert|crit|error|warn)\]/) {
      n++
      if (n > 5) next
      level = substr($0, RSTART + 1, RLENGTH - 2)
      where = "no site config line"
      if ($0 ~ /classbeat\.subdomain\.conf:[0-9]+$/) {
        line = $0; sub(/.*:/, "", line)
        where = "line " substr(line, 1, 7) " of the site config"
      }
      print "nginx -t: " level " at " where
    }
    END {
      if (n > 5) print "nginx -t: " (n - 5) " more error lines"
      if (n == 0) print "nginx -t: no nginx error line (is the Docker daemon running and the nginx image pullable? runbook section 9)"
    }' "$1" | while IFS= read -r l; do sfail "$l" || true; done
}

# nginx -t on FILE with stand-ins for swag's ssl.conf and the Cloudflare list. Needs the Docker daemon
# and the nginx image (pulled from Docker Hub on first use, runbook section 9). On a failure only
# nginx_log_summary is printed, never the log.
nginx_site_t() {   # nginx_site_t FILE
  mkdir -p "$tmp/nginx"
  printf 'ssl_certificate /tmp/cert.pem;\nssl_certificate_key /tmp/key.pem;\n' > "$tmp/nginx/ssl.conf"
  printf 'set_real_ip_from 173.245.48.0/20;\n' > "$tmp/nginx/cf_real-ip.conf"
  printf 'events {}\nhttp {\n  resolver 127.0.0.11;\n  include /config/nginx/proxy-confs/classbeat.subdomain.conf;\n}\n' > "$tmp/nginx/nginx.conf"
  cp "$1" "$tmp/nginx/site.conf" || { sfail "cannot copy it (readable by this user?)"; return 1; }
  nginx_t() {
    docker run --rm --network none \
      -v "$dir/nginx/site.conf:/config/nginx/proxy-confs/classbeat.subdomain.conf:ro" \
      -v "$dir/nginx/ssl.conf:/config/nginx/ssl.conf:ro" \
      -v "$dir/nginx/cf_real-ip.conf:/config/nginx/cf_real-ip.conf:ro" \
      -v "$dir/nginx/nginx.conf:/check/nginx.conf:ro" \
      "$nginx_image" sh -c 'set -e; mkdir -p /config/log/nginx
        openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=classbeat.example.test -days 1 \
          -keyout /tmp/key.pem -out /tmp/cert.pem 2>/dev/null
        nginx -t -q -c /check/nginx.conf' >"$tmp/nginx.log" 2>&1
  }
  if ! nginx_t; then
    # nginx -t opens its listening sockets; a kernel without IPv6 (some VMs) cannot open [::]:443.
    # Only then, check the rest without that one line and say so.
    grep -q 'Address family not supported' "$tmp/nginx.log" || { nginx_log_summary "$tmp/nginx.log"; sfail "nginx -t failed"; return 1; }
    sed -i '/listen \[::\]:443 ssl;/d' "$tmp/nginx/site.conf"
    nginx_t || { nginx_log_summary "$tmp/nginx.log"; sfail "nginx -t failed"; return 1; }
    echo "check-prod-compose: no IPv6 here: nginx -t ran without the [::]:443 listen line" >&2
  fi
}

