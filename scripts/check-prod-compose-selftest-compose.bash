# shellcheck shell=bash
# Self-test: every unsafe mutation of the rendered files is refused (repo mode).
# Sourced by check-prod-compose.sh, never run on its own; it shares that script's shell, variables and functions. Part of the check-prod-compose split (#763): no check lives here that is not in the original.
# Self-test: every mutation is unsafe and must be refused.
expect_refused() {   # expect_refused VARIANT BASE MUTATION [FRAGMENT of the message it must give]
  local variant="$1" base="$2" mutation="$3" why="${4:-}"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  if ("check_$variant" 2>"$tmp/refuse.log"); then
    echo "check-prod-compose: self-test: accepted an unsafe $variant file: $mutation" >&2
    exit 1
  fi
  if [[ -n "$why" ]] && ! grep -qF -- "$why" "$tmp/refuse.log"; then
    cat "$tmp/refuse.log" >&2
    echo "check-prod-compose: self-test: refused for another reason than '$why' ($variant): $mutation" >&2
    exit 1
  fi
}
expect_accepted() {   # expect_accepted VARIANT BASE MUTATION: a harmless change must still pass
  local variant="$1" base="$2" mutation="$3"
  J="$(jq "$mutation" <<<"$base")" \
    || { echo "check-prod-compose: self-test: a mutation does not apply: $mutation" >&2; exit 1; }
  ("check_$variant" 2>"$tmp/accept.log") \
    || { cat "$tmp/accept.log" >&2; echo "check-prod-compose: self-test: refused a harmless $variant change: $mutation" >&2; exit 1; }
}
for m in \
  '.services.caddy = {"image":"caddy:2.10.2","ports":[{"target":443,"published":"443"}],"logging":.services.web.logging}' \
  '.services.web.ports = [{"host_ip":"0.0.0.0","target":8000,"published":"8000"}]' \
  '.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8000"}]' \
  '.services.db.ports = [{"host_ip":"127.0.0.1","target":5432,"published":"5432"}]' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.0/24"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "0.0.0.0/0"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "173.245.48.5"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.10,172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = "172.29.90.130"' \
  '.services.web.environment.CLASSBEAT_TRUSTED_PROXIES = ""' \
  '.services.worker.networks.proxy = null' \
  '.services.db.networks["web-out"] = null' \
  '.networks.proxy.external = false' \
  '.networks.backend.internal = false' \
  '.services.web.read_only = false' \
  '.services.web.cap_drop = []' \
  'del(.services.web.healthcheck)' \
  '.services.web.secrets += [{"source":"anthropic_api_key","target":"anthropic_api_key"}]' \
  '.services.web.environment.CLASSBEAT_PUBLIC_BASE_URL = "http://classbeat.example.test"' \
  '.services.web.environment.CLASSBEAT_ENVIRONMENT = "dev"' \
  '.services.web.environment.CLASSBEAT_RUNNING_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SOUNDLESS_ENABLED = "true"' \
  '.services.worker.environment.CLASSBEAT_SWEDEBEAT_ENABLED = "true"' \
  '.services.mailpit = {"image":"axllent/mailpit","logging":.services.web.logging}' \
  '.volumes["caddy-data"] = {}' \
  '.services.extra = {"image":"busybox","networks":{"proxy":null},"logging":.services.web.logging}' \
  '.services.extra = {"image":"busybox","networks":{"web-out":null},"logging":.services.web.logging}' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/oauth_google_client_secret"}]' \
  '.services.migrate.secrets += [{"source":"oauth_github_client_secret"}]' \
  '.services.maint = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"secrets":[{"source":"oauth_microsoft_client_secret"}]}' \
  '.services.web.secrets |= map(select(.source != "oauth_github_client_secret"))' \
  '.services.web.secrets |= map(if .source == "oauth_google_client_secret" then .target = "/tmp/google" else . end)' \
  '.secrets.oauth_google_client_secret = {"name":"x","environment":"OAUTH_GOOGLE_CLIENT_SECRET"}' \
  '.secrets.oauth_github_client_secret = {"name":"x","content":"a-literal-secret"}' \
  '.secrets.oauth_microsoft_client_secret.file = "/etc/hostname"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET = "x"' \
  '.services.worker.environment.OAUTH_GITHUB_CLIENT_SECRET = "x"' \
  '.services.worker.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "check-google-client-id"' \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google"' \
  '.services.web.environment.CLASSBEAT_OAUTH_EXTRA = "x"' \
  '.services.worker.secrets += [{"source":"turnstile_secret_key"}]' \
  '.services.web.secrets |= map(select(.source != "turnstile_secret_key"))' \
  '.services.web.environment.CLASSBEAT_TURNSTILE_ENABLED = "true"' \
  '.services.web.environment.CLASSBEAT_TURNSTILE_FAKE = "true"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_FAKE = "false"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_ENABLED = "false"' \
  '.services.worker.environment.CLASSBEAT_TURNSTILE_SITE_KEY = ""'; do
  expect_refused shared "$shared" "$m"
done
# Secret files (#511): the group, the users, one name per file, no other way into secrets/.
for m in \
  'del(.services.web.group_add)' \
  'del(.services.migrate.group_add)' \
  '.services.worker.group_add = ["10011"]' \
  '.services.db.group_add = []' \
  'del(.services["db-functions"].group_add)' \
  'del(.services.db.user)' \
  '.services.db.user = "root"' \
  '.services["db-functions"].user = "0"' \
  '.services.migrate.user = "0:0"' \
  '.services.web.user = "root"' \
  '.services.worker.user = "root:10010"' \
  '.secrets.gsec = {"name":"x","file":.secrets.oauth_google_client_secret.file} | .services.worker.secrets += [{"source":"gsec"}]' \
  '.secrets.kek2 = {"name":"x","file":.secrets.connection_kek.file} | .services.migrate.secrets += [{"source":"kek2","target":"/run/secrets/kek2"}]' \
  '.secrets.app_secret_key = {"name":"x","environment":"APP_SECRET_KEY"}' \
  '.secrets.smtp_password.file = "/etc/hostname"' \
  '.services.worker.secrets += [{"source":"oauth_google_client_secret","target":"/run/secrets/app_secret_key"}]' \
  '.services.migrate.secrets |= map(.target = "/run/secrets/database_password")' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key")),"target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key") + "/"),"target":"/s"}]' \
  '.services.web.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key")),"target":"/x"}]' \
  '.services.web.volumes = [{"type":"bind","source":"/","target":"/host","read_only":true}]' \
  '.services.migrate.volumes = [{"type":"bind","source":.secrets.oauth_github_client_secret.file,"target":"/tmp/g"}]' \
  '.configs = {"c":{"file":.secrets.connection_kek.file}} | .services.web.configs = [{"source":"c","target":"/tmp/c"}]' \
  '.services.web.command = ["sh","-c","cat /run/secrets/oauth_google_*; exec python -m classbeat"]' \
  '.services.worker.entrypoint = ["sh","-c","cat /run/secrets/* >&2"]' \
  '.services.worker.command = "sh -c \"tar c secrets/ | nc x 1\""' \
  '.services.web.healthcheck.test = ["CMD","sh","-c","cat /run/secrets/app_secret_key"]' \
  '.services.db.image = "docker.io/library/postgres:17.11" | del(.services.db.user)' \
  '.services.db.image = "mirror.gcr.io/library/postgres@sha256:00" | .services.db.user = "1000"' \
  '.services.db.image = "example.test/pg:17" | del(.services.db.user)' \
  '.services.worker.volumes_from = ["web"]' \
  '.services.migrate.volumes_from = ["web:ro"]' \
  '.services.extra = {"image":.services.web.image,"networks":{"backend":null},"logging":.services.web.logging,"volumes_from":["container:classbeat-prod-web-1"]}' \
  '.services.worker.pid = "service:web"' \
  '.services.worker.pid = "host"' \
  '.services.migrate.pid = "container:classbeat-prod-web-1"' \
  '.services.worker.ipc = "service:web"' \
  '.services.db.ipc = "host"' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))}' \
  '.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}' \
  '.volumes["db-data"].driver = "local-persist"' \
  '.volumes["db-data"].external = true' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]' \
  '.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]' \
  '.services.migrate.volumes = [{"type":"bind","source":"/sys","target":"/s","read_only":true}]' \
  '.services.worker.volumes = [{"type":"bind","source":"/dev/sda1","target":"/d"}]' \
  '.services.worker.volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/link-to-secrets"),"target":"/s","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker"),"target":"/d","read_only":true}]' \
  '.services["db-functions"].volumes = [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/queue"),"target":"/queue","read_only":true}]' \
  '.services.db.volumes += [{"type":"bind","source":(.secrets.app_secret_key.file | rtrimstr("/secrets/app_secret_key") + "/docker/postgres/init"),"target":"/docker-entrypoint-initdb.d","read_only":true}]' \
  '.services.db.image = "postgres:17.11" | .services["db-functions"].image = "postgres:17.11"' \
  '.services["db-functions"].image = "ghcr.io/magpern/classbeat-db:9.9.9"' \
  '.services["db-functions"].user = "1000"' \
  '.services.worker.volumes = [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]' \
  '.services.worker.volumes = [{"type":"cluster","source":"x","target":"/c"}]' \
  '.services.worker.privileged = true' \
  '.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]' \
  '.services.db.cap_add = ["DAC_READ_SEARCH"]' \
  '.services.web.cap_add = ["SYS_PTRACE"]' \
  '.services.migrate.device_cgroup_rules = ["b 8:* rmw"]' \
  '.services.worker.use_api_socket = true' \
  '.volumes["db-data"].labels = {"x":"y"}'; do
  expect_refused shared "$shared" "$m"
done
# Default deny, on EVERY variant (#517; the #512 review's B1 to B5, the numeric-root rule, and keys and
# services nobody has heard of). Each entry is FRAGMENT|MUTATION: the mutated render must be refused
# and the message must contain FRAGMENT, so a mutation that trips some other rule, or does not apply,
# cannot pass for the rule it is for.
sd='(.secrets.app_secret_key.file | rtrimstr("/app_secret_key"))'
deny=(
  # B1: another service's mounts, a volume that is a bind of any host path, shared namespaces
  'services.worker.volumes_from|.services.worker.volumes_from = ["web"]'
  'services.migrate.volumes_from|.services.migrate.volumes_from = ["web:ro"]'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":'"$sd"'}'
  'volumes.db-data.driver_opts|.volumes["db-data"].driver_opts = {"type":"none","o":"bind","device":"/"}'
  'volumes.db-data.driver|.volumes["db-data"].driver = "local-persist"'
  'volumes.db-data.external|.volumes["db-data"].external = true'
  'services.worker.pid|.services.worker.pid = "service:web"'
  'services.worker.pid|.services.worker.pid = "host"'
  'services.migrate.pid|.services.migrate.pid = "container:classbeat-prod-web-1"'
  'services.worker.ipc|.services.worker.ipc = "service:web"'
  'services.db.ipc|.services.db.ipc = "host"'
  # B2: binds of /proc, /proc/1/root, the Docker socket and the secrets folder itself
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc","target":"/hostproc","read_only":true}]'
  'services.worker.volumes|.services.worker.volumes = [{"type":"bind","source":"/proc/1/root","target":"/h","read_only":true}]'
  'services.web.volumes|.services.web.volumes = [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'services.migrate.volumes|.services.migrate.volumes = [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc","target":"/hostproc"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/proc/1/root","target":"/h"}]'
  'no bind mounts|.services.db.volumes += [{"type":"bind","source":"/var/run/docker.sock","target":"/var/run/docker.sock"}]'
  'no bind mounts|.services.db.volumes += [{"type":"npipe","source":"//./pipe/docker_engine","target":"/p"}]'
  'bind|.services.db.volumes += [{"type":"bind","source":'"$sd"',"target":"/s"}]'
  # B3: configs (a file config is a bind of any host path)
  'configs|.configs = {"c":{"name":"x","file":"/proc"}} | .services.worker.configs = [{"source":"c","target":"/hostproc"}]'
  'configs|.configs = {"c":{"name":"x","file":"/"}} | .services.worker.configs = [{"source":"c","target":"/host"}]'
  'configs|.configs = {"c":{"name":"x","file":"/var/run/docker.sock"}} | .services.web.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":'"$sd"'}} | .services.worker.configs = [{"source":"c","target":"/s"}]'
  'configs|.configs = {"c":{"name":"x","file":"/proc"}}'
  'services.worker.configs|.services.worker.configs = [{"source":"c","target":"/s"}]'
  # B4: lifecycle hooks with their own user and privileges
  'services.web.post_start|.services.web.post_start = [{"command":["sh","-c","id"],"user":"root","privileged":true}]'
  'services.worker.pre_stop|.services.worker.pre_stop = [{"command":["sh","-c","id"]}]'
  # B5: a service behind profiles (the render holds it because of --profile "*"): a new service
  'services.spy|.services.spy = {"image":"busybox","profiles":["ops"],"pid":"host","privileged":true,"group_add":["10010"],"volumes":[{"type":"bind","source":"/","target":"/host"}],"logging":.services.web.logging}'
  'services.web.profiles|.services.web.profiles = ["ops"]'
  # root, however written (a numeric zero, a sign, a group, the name)
  'services.worker.user|.services.worker.user = "00"'
  'services.web.user|.services.web.user = "0000:0"'
  'services.migrate.user|.services.migrate.user = "10001:0"'
  'services.worker.user|.services.worker.user = "10001:root"'
  'services.web.user|.services.web.user = "10001"'
  'runs as root|.services.db.user = "+0"'
  'runs as root|.services.db.user = "0"'
  'runs as root|.services.db.user = "0:0"'
  'runs as root|.services["db-functions"].user = "0000:0"'
  'runs as root|.services.db.user = "postgres:0"'
  'runs as root|.services["db-functions"].user = "999:root"'
  'runs as root|.services.db.user = "root"'
  'runs as root|.services.db.user = "00"'
  'runs as postgres|.services.db.user = "1000"'
  'runs as postgres|del(.services["db-functions"].user)'
  'runs as root|.services.db.user = 0'
  # a brand-new key, a brand-new top-level key and a brand-new service
  'services.web.brand_new_compose_key|.services.web.brand_new_compose_key = true'
  'services.db.brand_new_compose_key|.services.db.brand_new_compose_key = {"a":1}'
  'brand_new_top_level|.brand_new_top_level = {}'
  'services.brand_new_service|.services.brand_new_service = {"image":.services.web.image,"logging":.services.web.logging}'
  # host-reaching keys the issue names
  'services.worker.userns_mode|.services.worker.userns_mode = "host"'
  'services.worker.cgroup|.services.worker.cgroup = "host"'
  'services.worker.cgroup_parent|.services.worker.cgroup_parent = "/"'
  'services.worker.uts|.services.worker.uts = "host"'
  'services.worker.runtime|.services.worker.runtime = "runc"'
  'services.worker.gpus|.services.worker.gpus = "all"'
  'services.worker.provider|.services.worker.provider = {"type":"x"}'
  'services.worker.env_file|.services.worker.env_file = [{"path":"./secrets/app_secret_key"}]'
  'services.worker.label_file|.services.worker.label_file = ["./secrets/app_secret_key"]'
  'services.worker.privileged|.services.worker.privileged = true'
  'services.db.devices|.services.db.devices = [{"source":"/dev/sda","target":"/dev/sda","permissions":"rwm"}]'
  'services.db.cap_add|.services.db.cap_add = ["DAC_READ_SEARCH"]'
  'services.migrate.device_cgroup_rules|.services.migrate.device_cgroup_rules = ["b 8:* rmw"]'
  'services.worker.use_api_socket|.services.worker.use_api_socket = true'
  'services.web.network_mode|.services.web.network_mode = "host"'
  # security_opt exactly no-new-privileges, cap_drop exactly ALL, group_add exactly 10010
  'security_opt is exactly|.services.worker.security_opt += ["seccomp=unconfined"]'
  'security_opt is exactly|.services.web.security_opt = ["no-new-privileges:true","apparmor=unconfined"]'
  'security_opt is exactly|.services.migrate.security_opt = ["no-new-privileges:true","systempaths=unconfined"]'
  'security_opt is exactly|.services.worker.security_opt = ["no-new-privileges:true","label=disable"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["NET_RAW"]'
  'cap_drop is exactly|.services.migrate.cap_drop = ["ALL","CHOWN"]'
  'group_add is exactly|.services.worker.group_add = ["10010","0"]'
  'group_add is exactly|.services.db.group_add = ["0"]'
  # nested blocks
  'services.worker.secrets[].mode|.services.worker.secrets[0].mode = 292'
  'services.worker.secrets[].uid|.services.worker.secrets[0].uid = "0"'
  'services.db.volumes[].bind|.services.db.volumes[0].bind = {"create_host_path":true}'
  'services.db.volumes[].volume.subpath|.services.db.volumes[0].volume.subpath = "x"'
  'services.web.healthcheck.start_interval|.services.web.healthcheck.start_interval = "1s"'
  'services.web.logging.options.tag|.services.web.logging.options.tag = "x"'
  'every service logs with json-file|.services.web.logging.driver = "syslog"'
  'services.web.ports[].x_extra|.services.web.ports = [{"host_ip":"127.0.0.1","target":8000,"published":"8080","x_extra":1}]'
  'services.worker.depends_on.*.restart|.services.worker.depends_on["db-functions"].restart = true'
  'services.worker.networks.*.ipv4_address|.services.worker.networks.egress = {"ipv4_address":"10.0.0.9"}'
  'networks.egress.driver_opts|.networks.egress.driver_opts = {"x":"y"}'
  'networks.backend.ipam.driver|.networks.backend.ipam.driver = "x"'
  'networks.egress.attachable|.networks.egress.attachable = true'
  'secrets.app_secret_key.x_extra|.secrets.app_secret_key.x_extra = 1'
  # a changed command, entrypoint or tmpfs
  'command, entrypoint and tmpfs|.services.web.entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.db.command = ["postgres","-c","x=y"]'
  'command, entrypoint and tmpfs|.services["db-functions"].entrypoint = ["sh","-c","id"]'
  'command, entrypoint and tmpfs|.services.web.tmpfs = ["/tmp:exec"]'
  'services.worker.tmpfs|.services.worker.tmpfs = ["/x"]'
  # variables by name (#610): what an env_file, however spelled, leaves in the render
  'services.worker.environment: 1 variable|.services.worker.environment[""] = "a-secret-file-line"'
  'services.web.environment: 1 variable|.services.web.environment.SPY = "1"'
  'services.db.environment: 1 variable|.services.db.environment.CLASSBEAT_DATABASE_URL = "x"'
  'services.worker.environment: 2 variable|.services.worker.environment += {"CLASSBEAT_MAINT_DATABASE_URL":"x","A":"b"}'
  'services.migrate.environment lacks CLASSBEAT_MIGRATIONS_DATABASE_URL|del(.services.migrate.environment.CLASSBEAT_MIGRATIONS_DATABASE_URL)'
  # top-level networks by key, and the project's own by name (#598 review, finding 5)
  'networks.spy|.networks.spy = {"name":"classbeat-prod-check_spy","ipam":{}}'
  'networks.backendx|.networks.backendx = {"name":"classbeat-prod-check_backendx","ipam":{}}'
  'networks.egress (external or renamed)|.networks.egress.name = "some-other-network"'
  'networks.egress (external or renamed)|.networks.egress.external = true'
  'networks.backend (external or renamed)|.networks.backend.name = "bridge"'
  # the CPU and memory ceilings of ADR 0026 stage 1 (#735), and the keys still default-deny
  'keep a cpus and a mem_limit ceiling|del(.services.worker.mem_limit)'
  'keep a cpus and a mem_limit ceiling|del(.services.web.cpus)'
  'keep a cpus and a mem_limit ceiling|.services.worker.cpus = 0'
  'services.migrate.cpus|.services.migrate.cpus = 1'
  'services.worker.memswap_limit|.services.worker.memswap_limit = "1"'
)
for v in shared loopback lan; do
  case "$v" in shared) base="$shared" ;; loopback) base="$loopback" ;; *) base="$lan" ;; esac
  for e in "${deny[@]}"; do
    expect_refused "$v" "$base" "${e#*|}" "${e%%|*}"
  done
  # the allow-list is not a refuse-everything: an extension key and the files as they are pass
  expect_accepted "$v" "$base" '.["x-note"] = {"a":1}'
  expect_accepted "$v" "$base" '.'
  # The allow-list itself passes the shipped files through the real-render check (no bridge overlay).
  expect_accepted real "$base" '.'
done
# A refused variable's name and value are never printed (an env_file of a secret file puts the secret
# in either, #610).
expect_refused shared "$shared" '.services.worker.environment["name-canary-610"] = "value-canary-610"' 'services.worker.environment: 1 variable'
if grep -qE 'name-canary-610|value-canary-610' "$tmp/refuse.log"; then
  echo "check-prod-compose: self-test: a refused variable's name or value was printed" >&2; exit 1
fi

# The client secret's value (the canary in the dummy file) anywhere in the rendered file.
expect_refused shared "$shared" ".services.web.labels = {\"note\": \"$oauth_canary-google\"}"
for m in \
  '.services.web.environment.CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID = "a-literal-in-compose"' \
  '.services.web.environment.CLASSBEAT_OAUTH_VISIBLE_PROVIDERS = "google,microsoft"' \
  'del(.services.web.environment.CLASSBEAT_OAUTH_MICROSOFT_CLIENT_ID)'; do
  expect_refused oauth_defaults "$defaults" "$m"
done
# The dev compose.yaml as it is (#521): the same rule as the production files.
check_oauth_files "$root" compose.yaml || exit 1
# The compose files as written (copies; the checkout's files are never changed).
mkdir "$tmp/files"
expect_files_refused() {
  cp "$tmp/compose.prod.yaml" "$tmp/compose.prod.loopback.yaml" "$root/compose.yaml" "$tmp/files/"
  sed -i -E "$2" "$tmp/files/$1"
  if (check_oauth_files "$tmp/files" compose.prod.yaml compose.prod.loopback.yaml compose.yaml 2>/dev/null); then
    echo "check-prod-compose: self-test: accepted an unsafe $1: $2" >&2; exit 1
  fi
}
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.prod.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.prod.yaml 's|^  oauth_google_client_secret:$|  oauth_google_client_secret: {content: abc}|'
expect_files_refused compose.prod.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
expect_files_refused compose.prod.loopback.yaml \
  's|^(      CLASSBEAT_TRUSTED_PROXIES:.*)$|\1\n      OAUTH_CLIENT_SECRET: abc|'
# shellcheck disable=SC2016  # a literal compose variable, not a shell one
expect_files_refused compose.yaml \
  's|^(      CLASSBEAT_OAUTH_VISIBLE_PROVIDERS:.*)$|\1\n      CLASSBEAT_OAUTH_GOOGLE_CLIENT_SECRET: ${OAUTH_GOOGLE_CLIENT_SECRET:-}|'
expect_files_refused compose.yaml 's|file: \./secrets/oauth_github_client_secret|environment: OAUTH_GITHUB_CLIENT_SECRET|'
