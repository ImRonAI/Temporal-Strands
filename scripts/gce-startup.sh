#!/usr/bin/env bash
# Ubuntu 24.04, x86_64. Run as root via Compute Engine startup-script metadata.
# Metadata: gwen-release-url (gs://BUCKET/OBJECT or HTTPS tar.gz), gwen-release-sha256,
# gwen-secret (projects/PROJECT/secrets/NAME/versions/VERSION).
# VM service account: cloud-platform OAuth scope, Secret Accessor on that
# secret, and Storage Object Viewer on the private release bucket.
# Bundle layout: app/ and strands-tools/src/{strands_graph_tool,skills}/.
# Only publish trusted bundles: installation executes code as the gwen user.
set -Eeuo pipefail
umask 027
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/local/bin:/usr/bin:/bin

meta() {
  curl --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 30 \
    -H 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/$1"
}

[[ $(id -u) == 0 ]] || { echo 'Run as root' >&2; exit 1; }
exec 9>/run/gwen-deploy.lock
flock -n 9 || exit 0

if [[ ${1:-} != --deploy ]]; then
  . /etc/os-release
  [[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || {
    echo 'This script requires Ubuntu 24.04 x86_64.' >&2; exit 1;
  }
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl jq git build-essential xz-utils \
    unzip lsof procps util-linux unattended-upgrades
  id gwen >/dev/null 2>&1 || useradd --create-home --shell /bin/bash gwen
  install -d -m 0755 /opt/gwen /opt/gwen/releases
  install -d -m 0750 -o root -g gwen /etc/gwen
  install -d -m 0750 -o gwen -g gwen /var/lib/gwen \
    /var/lib/gwen/temporal /var/lib/gwen/files /var/lib/gwen/repl \
    /var/lib/gwen/browser /var/lib/gwen/playwright

  # Install toolchains once. Do not silently upgrade infrastructure on reboot.
  setup=$(mktemp -d)
  trap 'rm -rf "$setup"' EXIT
  if ! test -x /usr/local/bin/node; then
    version=$(curl -fsS https://nodejs.org/dist/index.json | \
      jq -r '[.[] | select(.version | startswith("v24."))][0].version')
    file="node-${version}-linux-x64.tar.xz"
    curl -fsS "https://nodejs.org/dist/${version}/${file}" -o "$setup/$file"
    curl -fsS "https://nodejs.org/dist/${version}/SHASUMS256.txt" -o "$setup/sums"
    (cd "$setup"; sha256sum --check --ignore-missing sums)
    tar -xJf "$setup/$file" -C /usr/local --strip-components=1
  fi
  command -v pnpm >/dev/null || npm install --global pnpm@11.18.0
  if ! command -v uv >/dev/null; then
    curl -fsS https://astral.sh/uv/install.sh -o "$setup/uv-install.sh"
    UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh "$setup/uv-install.sh"
  fi
  if ! command -v temporal >/dev/null; then
    curl -fLsS 'https://temporal.download/cli/archive/latest?platform=linux&arch=amd64' \
      -o "$setup/temporal.tar.gz"
    tar -xzf "$setup/temporal.tar.gz" -C "$setup" temporal
    install -m 0755 "$setup/temporal" /usr/local/bin/temporal
  fi
  # Keep a copy: GCE may supply the original from a temporary location.
  if [[ $(realpath "$0") != /usr/local/sbin/gwen-startup ]]; then
    install -m 0700 "$(realpath "$0")" /usr/local/sbin/gwen-startup
  fi

  cat >/etc/systemd/system/gwen-temporal.service <<'UNIT'
[Unit]
Description=Gwen personal Temporal server (persistent dev server, not production)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
User=gwen
Group=gwen
ExecStart=/usr/local/bin/temporal server start-dev --ip 127.0.0.1 --db-filename /var/lib/gwen/temporal/history.db
Restart=always
RestartSec=5
TimeoutStopSec=120
NoNewPrivileges=true
UMask=0027
[Install]
WantedBy=multi-user.target
UNIT

  for service in worker api web; do
    case "$service" in
      worker) cwd=/opt/gwen/current/app/orchestrator
        command='/opt/gwen/current/app/orchestrator/.venv/bin/python -u run_worker.py' ;;
      api) cwd=/opt/gwen/current/app/orchestrator
        command='/opt/gwen/current/app/orchestrator/.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8787' ;;
      web) cwd=/opt/gwen/current/app
        command='/usr/local/bin/pnpm start --hostname 127.0.0.1 --port 3000' ;;
    esac
    cat >"/etc/systemd/system/gwen-${service}.service" <<UNIT
[Unit]
Description=Gwen ${service}
After=network-online.target gwen-temporal.service
Wants=network-online.target gwen-temporal.service
PartOf=gwen-temporal.service
ConditionPathExists=/opt/gwen/current/app/package.json
StartLimitIntervalSec=0
[Service]
User=gwen
Group=gwen
WorkingDirectory=${cwd}
EnvironmentFile=/etc/gwen/runtime.env
Environment=HOME=/home/gwen
Environment=PATH=/opt/gwen/current/app/orchestrator/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=NODE_ENV=production
Environment=TEMPORAL_ADDRESS=127.0.0.1:7233
Environment=ORCHESTRATOR_URL=http://127.0.0.1:8787
Environment=STRANDS_BROWSER_HEADLESS=true
Environment=PLAYWRIGHT_BROWSERS_PATH=/var/lib/gwen/playwright
Environment=STRANDS_BROWSER_USER_DATA_DIR=/var/lib/gwen/browser
Environment=AGENT_FILE_STORE_DIR=/var/lib/gwen/files
Environment=PYTHON_REPL_PERSISTENCE_DIR=/var/lib/gwen/repl
ExecStartPre=/usr/local/bin/temporal operator cluster health --address 127.0.0.1:7233 --command-timeout 10s
ExecStart=${command}
Restart=always
RestartSec=10
TimeoutStopSec=180
KillSignal=SIGTERM
NoNewPrivileges=true
UMask=0027
[Install]
WantedBy=multi-user.target
UNIT
  done

  cat >/etc/systemd/system/gwen-update.service <<'UNIT'
[Unit]
Description=Deploy approved Gwen release from VM metadata
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/gwen-startup --deploy
TimeoutStartSec=7200
UNIT
  cat >/etc/systemd/system/gwen-update.timer <<'UNIT'
[Unit]
Description=Check for an approved Gwen release daily
[Timer]
OnCalendar=*-*-* 04:00:00 UTC
RandomizedDelaySec=900
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "false";
APT
  systemctl daemon-reload
  systemctl enable gwen-temporal gwen-worker gwen-api gwen-web gwen-update.timer
  systemctl start gwen-temporal gwen-update.timer
  rm -rf "$setup"
  trap - EXIT
fi

url=$(meta instance/attributes/gwen-release-url)
sha=$(meta instance/attributes/gwen-release-sha256)
secret=$(meta instance/attributes/gwen-secret)
[[ ( $url == https://* || $url == gs://*/* ) && $sha =~ ^[a-f0-9]{64}$ ]] || {
  echo 'Set a gs:// or HTTPS gwen-release-url and its lowercase SHA256 metadata.' >&2; exit 1;
}
[[ $secret =~ ^projects/[^/]+/secrets/[^/]+/versions/[^/]+$ ]] || {
  echo 'Invalid gwen-secret resource name.' >&2; exit 1;
}
release="/opt/gwen/releases/$sha"
old=$(readlink -f /opt/gwen/current || true)
# Updating a secret alone requires a new release or an explicit service restart.
if [[ $old == "$release" && -f $release/.ready ]]; then
  systemctl start gwen-temporal gwen-worker gwen-api gwen-web
  exit 0
fi

stage=$(mktemp -d /opt/gwen/releases/.download-XXXXXX)
trap 'rm -rf "$stage"' EXIT
if [[ $url == gs://* ]]; then
  object=${url#gs://}
  bucket=${object%%/*}
  object=$(printf '%s' "${object#*/}" | jq -sRr @uri)
  token=$(meta instance/service-accounts/default/token | jq -er .access_token)
  curl -fsS --retry 3 --max-time 600 -H "Authorization: Bearer $token" \
    "https://storage.googleapis.com/storage/v1/b/${bucket}/o/${object}?alt=media" \
    -o "$stage/release.tar.gz"
  unset token
else
  curl --fail --location --silent --show-error --retry 3 --max-time 600 "$url" -o "$stage/release.tar.gz"
fi
printf '%s  %s\n' "$sha" "$stage/release.tar.gz" | sha256sum --check
# Extract as an unprivileged user, never as root. Do not include .env or venvs.
chown gwen:gwen "$stage"
chmod 0750 "$stage"
chmod 0644 "$stage/release.tar.gz"
install -d -o gwen -g gwen "$stage/source"
runuser -u gwen -- tar --no-same-owner --no-same-permissions \
  -xzf "$stage/release.tar.gz" -C "$stage/source"
test -f "$stage/source/app/package.json"
test -f "$stage/source/strands-tools/src/strands_graph_tool/__init__.py"
test -d "$stage/source/strands-tools/src/skills"
# Retry failed preparation on the next timer, without deleting active code.
if [[ -d $release && ! -f $release/.ready && $release != "$old" ]]; then
  rm -rf "$release"
fi
test ! -e "$release" || { echo "Release already exists: $release" >&2; exit 1; }
mv "$stage/source" "$release"

# The local graph source currently has no build metadata. Package the CURRENT
# source in the release, not its old bundled wheel. The original is untouched.
if [[ ! -f $release/strands-tools/pyproject.toml ]]; then
  cat >"$release/strands-tools/pyproject.toml" <<'TOML'
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"
[project]
name = "strands-heterogeneous-graph-tool"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["pydantic>=2.10", "strands-agents-tools>=0.8.5,<1", "strands-agents>=1.50.2,<2", "strictyaml>=1.0.0"]
[tool.setuptools.packages.find]
where = ["src"]
include = ["strands_graph_tool*"]
TOML
fi
chown -R gwen:gwen "$release"
runuser -u gwen -- bash -c '
  set -e
  cd "$1/app/orchestrator"
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
  .venv/bin/python -c "import strands_graph_tool, run_worker, server"
  .venv/bin/python -m pytest tests -q
  cd ..
  pnpm install --frozen-lockfile
  pnpm exec next typegen
  pnpm exec tsc --noEmit
  pnpm lint
  pnpm exec vitest run --exclude "**/.worktrees/**" --exclude "**/.kilo/**"
' bash "$release"
"$release/app/orchestrator/.venv/bin/playwright" install-deps chromium
runuser -u gwen -- env PLAYWRIGHT_BROWSERS_PATH=/var/lib/gwen/playwright \
  "$release/app/orchestrator/.venv/bin/playwright" install chromium

# Fetch secrets using the VM service account. Never put API keys in metadata.
token=$(meta instance/service-accounts/default/token | jq -er .access_token)
curl -fsS -H "Authorization: Bearer $token" \
  "https://secretmanager.googleapis.com/v1/${secret}:access" | \
  jq -er '.payload.data' | base64 --decode >"$stage/runtime.env"
unset token
test -s "$stage/runtime.env"
chmod 0640 "$stage/runtime.env"
chown root:gwen "$stage/runtime.env"
# NEXT_PUBLIC_* values must also be present during the production build.
systemd-run --quiet --wait --pipe --collect --unit="gwen-build-${sha:0:12}" \
  -p User=gwen -p Group=gwen -p "WorkingDirectory=$release/app" \
  -p "EnvironmentFile=$stage/runtime.env" \
  -p 'Environment=HOME=/home/gwen PATH=/usr/local/bin:/usr/bin:/bin' \
  /usr/local/bin/pnpm build

systemctl stop gwen-web gwen-api gwen-worker
if [[ -f /etc/gwen/runtime.env ]]; then
  cp -p /etc/gwen/runtime.env "$stage/previous.env"
fi
install -m 0640 -o root -g gwen "$stage/runtime.env" /etc/gwen/runtime.env
ln -sfn "$release" /opt/gwen/current.next
mv -Tf /opt/gwen/current.next /opt/gwen/current
systemctl start gwen-temporal gwen-worker gwen-api gwen-web

healthy=false
for attempt in {1..60}; do
  if curl -fsS --max-time 5 http://127.0.0.1:8787/health | jq -e '.status == "ok" and .pollers == true' >/dev/null && \
    curl -fsS --max-time 5 http://127.0.0.1:3000/ >/dev/null; then
    healthy=true
    break
  fi
  sleep 5
done
if [[ $healthy != true ]]; then
  echo 'Release failed health checks. Restoring previous code if available.' >&2
  systemctl stop gwen-web gwen-api gwen-worker
  if [[ -n $old && -d $old ]]; then
    ln -sfn "$old" /opt/gwen/current.previous
    mv -Tf /opt/gwen/current.previous /opt/gwen/current
    if [[ -f $stage/previous.env ]]; then
      cp -p "$stage/previous.env" /etc/gwen/runtime.env
    fi
    systemctl start gwen-worker gwen-api gwen-web
  else
    rm -f /opt/gwen/current
  fi
  exit 1
fi
touch "$release/.ready"
echo "Gwen deployed: $sha"
echo 'Private UI: 127.0.0.1:3000. Verify Temporal pollers and a real tool turn.'
