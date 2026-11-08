# shellcheck shell=bash
# The compose rules shared by the variants: allow-list of keys, networks, ports, variables.
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
J=""
fail() { echo "check-prod-compose: $*" >&2; return 1; }
q() { jq -e "$@" <<<"$J" >/dev/null; }

# One private IPv4 address (10/8, 172.16/12, 192.168/16), surrounding blanks aside as the app strips
# them: never a range, a list, a name, an IPv6 or a public address (#495).
proxy_ip_ok() {
  local v="$1" o
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  [[ "$v" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || return 1
  for o in "${BASH_REMATCH[@]:1}"; do (( o <= 255 )) || return 1; done
  local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"
  (( a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) ))
}

# Both variants. Each check returns 1 with its reason on the first violation.
check_common() {
  q '.services | has("caddy") | not' || fail "no caddy: swag terminates TLS (#492)" || return 1
  q '(.volumes // {} | keys) == ["db-data"]' || fail "the only named volume is db-data" || return 1
  q '.services | has("mailpit") | not' || fail "no mailpit in production" || return 1
  q '[.services | to_entries[] | select(.value.ports // [] | length > 0) | .key] | . == [] or . == ["web"]' \
    || fail "only web may publish a port (and only with the loopback overlay)" || return 1
  q '.services.db | (.ports // []) | length == 0' || fail "db must not publish ports" || return 1
  q '.networks.backend.internal == true' || fail "the backend network must be internal" || return 1
  q '[.services.db, .services.migrate, .services["db-functions"] | .networks | keys] | all(. == ["backend"])' \
    || fail "db, migrate and db-functions live on backend only" || return 1
  q '(.services.worker.networks | keys) == ["backend","egress"]' \
    || fail "worker joins backend and egress only" || return 1
  q '[.services.db.image, .services["db-functions"].image] | all(. == "ghcr.io/magpern/classbeat-db:0.0.0")' \
    || fail "db and db-functions run the release's classbeat-db image (ADR 0020)" || return 1
  q '.services.web.read_only == true' || fail "web must be read-only" || return 1
  q '[.services.web, .services.worker] | all((.cap_drop // []) == ["ALL"] and ((.security_opt // []) | index("no-new-privileges:true")))' \
    || fail "web and worker drop every capability and set no-new-privileges" || return 1
  q '[.services.web, .services.db] | all(.healthcheck.test != null)' \
    || fail "web and db need their healthchecks" || return 1
  q '[.services.web.secrets[].source] | (index("anthropic_api_key") // -1) < 0' \
    || fail "web must not mount a model key" || return 1
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("swedebeat_app")) | .key] == ["web"]' \
    || fail "swedebeat_app belongs to web only" || return 1
  q '.services.web.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
    || fail "Swedebeat must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED == "false"' \
    || fail "Swedebeat must be off by default on the worker too" || return 1
  q '.services.web.environment.CLASSBEAT_RUNNING_ENABLED == "false"' \
    || fail "running classes must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_RUNNING_ENABLED == "false"' \
    || fail "running classes must be off by default on the worker too" || return 1
  q '.services.web.environment.CLASSBEAT_SOUNDLESS_ENABLED == "false"' \
    || fail "soundless classes must be off by default" || return 1
  q '.services.worker.environment.CLASSBEAT_SOUNDLESS_ENABLED == "false"' \
    || fail "soundless classes must be off by default on the worker too" || return 1
  q '.services.web.environment.CLASSBEAT_MOWL_QR_ENABLED == "false"' \
    || fail "the MOWL QR sign-in must be off by default" || return 1
  q '.services.worker.environment | has("CLASSBEAT_MOWL_QR_ENABLED") | not' \
    || fail "the MOWL QR sign-in belongs to web only" || return 1
  # ADR 0025 (#724): Turnstile's secret on web only, its switch and site key web's only, off by
  # default until the owner's live check (PR 3 of #721 flips the default), and the fake never in
  # a production file, whatever service.
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("turnstile_secret_key")) | .key] == ["web"]' \
    || fail "turnstile_secret_key belongs to web only" || return 1
  q '.services.web.environment.CLASSBEAT_TURNSTILE_ENABLED == "false"' \
    || fail "Turnstile must be off by default (until #721's last PR)" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | keys | map(select(startswith("CLASSBEAT_TURNSTILE_"))) | length > 0) | .key] == ["web"]' \
    || fail "the Turnstile settings belong to web only" || return 1
  q '[.services[] | .environment // {} | has("CLASSBEAT_TURNSTILE_FAKE")] | any | not' \
    || fail "CLASSBEAT_TURNSTILE_FAKE is never set in a production file" || return 1
  # ADR 0019 (#790): passkeys are off by default (the owner switches them on in .env after
  # choosing the RP ID) and their two settings belong to web only.
  q '.services.web.environment.CLASSBEAT_PASSKEYS == "false"' \
    || fail "passkeys must be off by default" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | keys | map(select(. == "CLASSBEAT_PASSKEYS" or . == "CLASSBEAT_WEBAUTHN_RP_ID")) | length > 0) | .key] == ["web"]' \
    || fail "the passkey settings belong to web only" || return 1
  q '.services.web.environment.CLASSBEAT_LOCALES | tostring | length > 0' \
    || fail "the offered languages are set on web" || return 1
  q '.services.worker.environment | has("CLASSBEAT_LOCALES") | not' \
    || fail "the offered languages belong to web only" || return 1
  q '[.services.worker.secrets[].source] | index("database_maint_password") == null' \
    || fail "worker must not mount the maint password" || return 1
  q '[.services | to_entries[] | select(.value.secrets // [] | map(.source) | index("postgres_password")) | .key] | sort == ["db","db-functions"]' \
    || fail "postgres_password belongs to db and db-functions only" || return 1
  q '[.services | to_entries[] | select(.value.environment // {} | has("CLASSBEAT_SMTP_CA_FILE")) | .key] == ["web"]' \
    || fail "CLASSBEAT_SMTP_CA_FILE belongs to web only (migrate would fail on an unmounted file)" || return 1
  q '.services.web.environment.CLASSBEAT_SMTP_CA_FILE == "/run/smtp-ca/bridge.pem"' \
    || fail "web must get SMTP_CA_FILE" || return 1
  q '.services.web.environment.CLASSBEAT_ENVIRONMENT == "prod"' || fail "web must run as prod" || return 1
  q '.services.web.environment.CLASSBEAT_PUBLIC_BASE_URL == "https://classbeat.example.test"' \
    || fail "the public origin is https:// plus CLASSBEAT_DOMAIN" || return 1
  # One private IPv4 address, never a range or a list: the one peer whose X-Forwarded-For counts.
  proxy_ip_ok "$(jq -r '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES // "" | tostring' <<<"$J")" \
    || fail "CLASSBEAT_TRUSTED_PROXIES must be one private IPv4 address" || return 1
  q '.networks["web-out"].ipam.config == [{"subnet":"172.29.80.0/24","gateway":"172.29.80.1"}] and (.networks["web-out"].external // false | not)' \
    || fail "web-out keeps its fixed subnet and gateway" || return 1
  q '[.services | to_entries[] | select(.value.networks // {} | has("web-out")) | .key] == ["web"]' \
    || fail "only web joins web-out" || return 1
  q '[.services[] | select((.logging.options["max-size"] // null) == null)] | length == 0' \
    || fail "every service needs log rotation" || return 1
  check_keys || return 1
  check_oauth || return 1
  check_secret_mounts || return 1
}

# Default deny (#517, after the #512 reviews B1 to B5 found one channel after another). Every key of
# the rendered file (rendered with every profile) must be on this allow-list, which is exactly what
# compose.prod.yaml and its two overlays use today: at the top level, per service BY NAME (a new
# service is refused) and in each nested block. Anything else is refused by its name: configs,
# post_start and pre_stop, profiles, pid, ipc, privileged, cap_add, devices, volumes_from, userns_mode,
# cgroup, cgroup_parent, uts, runtime, gpus, provider, env_file, a bind or a driver option on a
# volume, a key Compose adds later. Adding a key or a service to the production files needs a change
# here, reviewed with it (runbook section 13). The value checks below and in check_secret_mounts still
# apply to the allowed keys. The render drops env_file but copies its variables into `environment`,
# which is allowed by variable name per service (#610), and keeps label_file and profiles (refused here);
# include and extends are resolved into it. check_prod_file_text is a fast lint of the files' text for the
# same keys, not the check: a quoted or explicit (`? env_file`) key gets past a line match. Top-level
# networks are allowed by key too (#598 review finding 5).
# shellcheck disable=SC2016  # jq variables, not shell ones
check_keys() {
  local bad
  bad="$(jq -r '
    def allowed_services: {
      "db": ["command","entrypoint","environment","group_add","healthcheck","image","logging","networks",
             "restart","secrets","user","volumes"],
      "db-functions": ["command","depends_on","entrypoint","group_add","image","logging","networks","restart",
             "secrets","tmpfs","user"],
      "migrate": ["cap_drop","command","depends_on","entrypoint","environment","group_add","image","logging",
             "networks","restart","secrets","security_opt"],
      "web": ["cap_drop","command","cpus","depends_on","entrypoint","environment","expose","group_add","healthcheck",
             "image","logging","mem_limit","networks","ports","read_only","restart","secrets","security_opt","tmpfs"],
      "worker": ["cap_drop","command","cpus","depends_on","entrypoint","environment","group_add","image","logging",
             "mem_limit","networks","restart","secrets","security_opt","stop_grace_period"]
    };
    # The keys of $o that are not in $allowed, named with their path; a non-mapping is refused whole.
    def extra($o; $allowed; $where):
      if $o == null then empty
      elif ($o | type) != "object" then "\($where) (not a mapping)"
      else ($o | keys - $allowed)[] | "\($where).\(.)" end;
    ((keys | map(select(startswith("x-") | not))) - ["name","services","networks","volumes","secrets"])[],
    (.services | to_entries[] | .key as $n | .value as $v
      | if (allowed_services | has($n)) | not then "services.\($n)"
        else
          extra($v; allowed_services[$n]; "services.\($n)"),
          ($v.secrets // [] | .[] | extra(.; ["source","target"]; "services.\($n).secrets[]")),
          ($v.volumes // [] | .[] | extra(.; ["type","source","target","volume"]; "services.\($n).volumes[]"),
             extra(.volume; []; "services.\($n).volumes[].volume")),
          extra($v.healthcheck; ["test","interval","timeout","retries","start_period"]; "services.\($n).healthcheck"),
          extra($v.logging; ["driver","options"]; "services.\($n).logging"),
          extra($v.logging.options; ["max-size","max-file"]; "services.\($n).logging.options"),
          ($v.depends_on // {} | .[] | extra(.; ["condition","required"]; "services.\($n).depends_on.*")),
          ($v.networks // {} | .[] | extra(.; ["aliases"]; "services.\($n).networks.*")),
          ($v.ports // [] | .[] | extra(.; ["host_ip","mode","protocol","published","target"]; "services.\($n).ports[]"))
        end),
    (.networks // {} | to_entries[] | .key as $n
      | extra(.value; ["name","external","internal","ipam"]; "networks.\($n)"),
        extra(.value.ipam; ["config"]; "networks.\($n).ipam"),
        (.value.ipam.config // [] | .[] | extra(.; ["subnet","gateway"]; "networks.\($n).ipam.config[]"))),
    (.volumes // {} | to_entries[] | extra(.value; ["name"]; "volumes.\(.key)")),
    (.secrets // {} | to_entries[] | extra(.value; ["name","file"]; "secrets.\(.key)"))
  ' <<<"$J")" || fail "the rendered file could not be read as compose keys" || return 1
  [[ -z "$bad" ]] || fail "not on the allow-list of compose keys (check_keys in scripts/check-prod-compose.sh): $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # Each service's variables, by name (#610). The render drops env_file but copies the file into
  # `environment`, however the key was spelled (`"env_file"`, `? env_file`, a flow mapping), so the
  # text rule (check_prod_file_text) is only a fast lint and this is the check: a variable the files do
  # not set is refused. Its name is never printed: an env_file of a secret file gives a variable whose
  # name or value is the secret (a line without `=` becomes the name "" with the line as its value).
  bad="$(jq -r '
    def app: ["CLASSBEAT_ADMIN_EMAILS","CLASSBEAT_CONNECTION_KEK_ID","CLASSBEAT_DATABASE_URL",
      "CLASSBEAT_DEEPSEEK_ENABLED","CLASSBEAT_ENVIRONMENT","CLASSBEAT_LOG_LEVEL","CLASSBEAT_MAIL_SUPPORT_ADDRESS","CLASSBEAT_PUBLIC_BASE_URL",
      "CLASSBEAT_SIGNUP_MODE","CLASSBEAT_SMTP_HOST","CLASSBEAT_SMTP_PORT","CLASSBEAT_SMTP_SENDER",
      "CLASSBEAT_SMTP_TLS","CLASSBEAT_SMTP_USERNAME"];
    def flags: ["CLASSBEAT_RUNNING_ENABLED","CLASSBEAT_SOUNDLESS_ENABLED","CLASSBEAT_SWEDEBEAT_ENABLED"];
    def allowed_env: {
      "db": ["POSTGRES_DB","POSTGRES_PASSWORD_FILE"],
      "db-functions": [],
      "migrate": (app + ["CLASSBEAT_MIGRATIONS_DATABASE_URL"]),
      "worker": (app + flags + ["CLASSBEAT_MODEL_PROVIDER"]),
      "web": (app + flags + ["CLASSBEAT_MAINT_DATABASE_URL","CLASSBEAT_OAUTH_GITHUB_CLIENT_ID",
        "CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID","CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID","CLASSBEAT_OAUTH_VISIBLE_PROVIDERS",
        "CLASSBEAT_LOCALES","CLASSBEAT_MOWL_QR_ENABLED","CLASSBEAT_SMTP_CA_FILE","CLASSBEAT_TRUSTED_PROXIES",
        "CLASSBEAT_TURNSTILE_ENABLED","CLASSBEAT_TURNSTILE_SITE_KEY","CLASSBEAT_PASSKEYS","CLASSBEAT_WEBAUTHN_RP_ID"])
    };
    .services | to_entries[] | .key as $n | (.value.environment // {}) as $e
    | if ($e | type) != "object" then "services.\($n).environment (not a mapping)"
      else (($e | keys) - (allowed_env[$n] // [])) as $x
        | ((allowed_env[$n] // []) - ($e | keys)) as $m
        | (if ($x | length) > 0 then "services.\($n).environment: \($x | length) variable(s) the production files do not set (names not printed)" else empty end),
          (if ($m | length) > 0 then "services.\($n).environment lacks \($m | join(" "))" else empty end)
      end' <<<"$J")" || fail "the rendered environment could not be read" || return 1
  [[ -z "$bad" ]] || fail "variables not on the allow-list (an env_file, however spelled, is copied into environment; check_keys): $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # Top-level networks by key (#598 review, finding 5): only the ones the shipped files use. proxy is
  # the owner's external network; every other one is the project's own (not external, its default
  # name `<project>_<key>`), so a `name:` cannot join web or the worker to some other existing network.
  bad="$(jq -r '.name as $p | .networks // {} | to_entries[]
      | if (.key | IN("backend","egress","proxy","web-out")) | not then "networks.\(.key)"
        elif .key != "proxy" and ((.value.external // false) or .value.name != ($p + "_" + .key))
        then "networks.\(.key) (external or renamed)" else empty end' <<<"$J")" \
    || fail "the rendered networks could not be read" || return 1
  [[ -z "$bad" ]] || fail "networks: only backend, egress, web-out (the project's own) and the external proxy: $(tr '\n' ' ' <<<"$bad")" \
    || return 1
  # The values of the allowed keys that reach the host or another container.
  q '[.services[] | .logging.driver] | all(. == "json-file")' || fail "every service logs with json-file" || return 1
  q '[.services[] | select(has("security_opt")) | .security_opt] | all(. == ["no-new-privileges:true"])' \
    || fail "security_opt is exactly [\"no-new-privileges:true\"] (no seccomp, apparmor, systempaths or label option)" || return 1
  q '[.services[] | select(has("cap_drop")) | .cap_drop] | all(. == ["ALL"])' \
    || fail "cap_drop is exactly [\"ALL\"]" || return 1
  jq -e --arg g "$secrets_gid" '[.services[] | select(has("group_add")) | .group_add | map(tostring)] | all(. == [$g])' <<<"$J" >/dev/null \
    || fail "group_add is exactly [\"$secrets_gid\"] (no other group, root's included)" || return 1
  # ADR 0026 stage 1 (#735): web and the worker keep a CPU and a memory ceiling (a positive number
  # of CPUs; mem_limit as the render gives it, in bytes), so a mix analysis cannot starve the box.
  q '[.services.web, .services.worker | (.cpus | tonumber? // 0) > 0 and ((.mem_limit // "") | tostring | test("^[1-9][0-9]*$"))] | all' \
    || fail "web and worker keep a cpus and a mem_limit ceiling (compose.prod.yaml, runbook section 5c)" || return 1
  # The command, entrypoint and tmpfs mounts are the ones the files have: a changed one is a reviewed
  # change to this list (an entrypoint or a tmpfs option can reach what the keys above cannot).
  q 'def known: {
       "db": {}, "web": {"tmpfs": ["/tmp"]}, "worker": {"command": ["python","-m","classbeat.worker"]},
       "migrate": {"command": ["alembic","upgrade","head"]},
       "db-functions": {"entrypoint": ["sh","/queue/db-functions.sh"], "tmpfs": ["/run/pgpass:mode=0700,uid=999,gid=999"]}
     };
     [.services | to_entries[] | .key as $n | .value as $v | known[$n] as $k
       | ["command","entrypoint","tmpfs"][] | select(($v[.] // null) != ($k[.] // null))] | length == 0' \
    || fail "command, entrypoint and tmpfs are the ones in the production files" || return 1
}

