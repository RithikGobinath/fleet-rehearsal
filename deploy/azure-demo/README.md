# Azure live demo

Run the hosted Fleet Rehearsal stack on one x86-64 Ubuntu VM with k3s. The overlay uses one worker replica, one PostgreSQL pod, and one Kafka broker. Its persistent volumes use k3s local-path storage on the VM. It uses the checked-in synthetic inventory and topology. Use an Azure public IP DNS label for the browser address and keep PostgreSQL, Kafka, and the Kubernetes API private.

The VM used for the November 19 rehearsal was running Ubuntu 24.04 and k3s v1.35.8+k3s1. Its application checkout was at [commit `05a038e`](https://github.com/RithikGobinath/fleet-rehearsal/commit/05a038e0d6b63020be88d72cd1624b0e05abe212). The checked-in overlay was committed after November 19 to reproduce the setup used for that rehearsal.

## Azure review

Use a dedicated resource group in the Azure for Students subscription. The demo used a North Central US `Standard_B2as_v2` VM (2 vCPUs, 8 GiB RAM). Choose Ubuntu Server 24.04 LTS x64, a 64 GiB Standard SSD, and a Standard static public IPv4 address with a DNS label. Use SSH public-key authentication. Allow inbound TCP 443 and temporary TCP 80 for certificate validation. Restrict TCP 22 to the operator's IP. Keep the k3s API port 6443 closed to public ingress.

Check the current estimate and your quota before provisioning. Enable daily auto-shutdown. Retaining the disk or public IP after deallocating the VM can still incur charges. [Azure VM states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)

## Auth0 and HTTPS

Create a Single Page Application and an API in a demo Auth0 tenant. Choose an API identifier such as `https://fleet-rehearsal.demo/api` and use RS256 access tokens. Set the SPA callback and logout URLs to `https://<public-host>/` and its allowed web origin to `https://<public-host>`. Enable a database connection for the SPA, create three dedicated test users, and record their Auth0 user IDs. Use distinct non-personal display names for captures. The backend validates the token and maps each issuer and subject pair to its seeded site grants. The SPA uses a public client ID and requires no client secret.

Once the Azure DNS label resolves to the VM, acquire a certificate for that hostname with Certbot standalone over port 80 before starting k3s. Use the resulting certificate and key as the `fleet-tls` Kubernetes Secret. The Azure DNS label supplies the public hostname. [Azure DNS labels](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/public-ip-addresses/#domain-name-label) · [Auth0 Angular setup](https://auth0.com/docs/quickstart/spa/angular/interactive)

## Deploy

On the approved Ubuntu VM, set `demo_host` to its Azure DNS hostname. Issue the certificate before k3s starts its ingress controller.

```bash
sudo apt-get update
sudo apt-get install -y certbot curl docker.io git openssl python3
sudo certbot certonly --standalone -d "${demo_host:?}"
curl -fsSL https://get.k3s.io | sudo env INSTALL_K3S_VERSION='v1.35.8+k3s1' sh -s - server --secrets-encryption
```

Use the selected Fleet Rehearsal source checkout and run the following in Bash from the repository root. Set `auth0_issuer` (HTTPS URL with a trailing slash), `auth0_audience` (API identifier), and `auth0_client_id` (public SPA client ID) from the tenant.

```bash
set -euo pipefail
: "${demo_host:?}" "${auth0_issuer:?}" "${auth0_audience:?}" "${auth0_client_id:?}"
k() { sudo k3s kubectl "$@"; }

sudo docker build --target runtime -f api/Dockerfile -t fleet-api:azure-demo .
sudo docker build -f web/Dockerfile -t fleet-web:azure-demo .
sudo docker save fleet-api:azure-demo fleet-web:azure-demo | sudo k3s ctr images import -
k create namespace fleet --dry-run=client -o yaml | k apply -f -
if ! k -n fleet get secret fleet-runtime-secrets >/dev/null 2>&1; then
  db_password="$(openssl rand -hex 24)"
  printf 'POSTGRES_PASSWORD=%s\nDATABASE_URL=postgresql://fleet:%s@postgres:5432/fleet\n' \
    "$db_password" "$db_password" | k -n fleet create secret generic fleet-runtime-secrets --from-env-file=/dev/stdin
  unset db_password
fi
k apply -k deploy/azure-demo
k -n fleet patch configmap fleet-runtime --type merge -p \
  "$(python3 -c 'import json,sys; print(json.dumps({"data":dict(zip(("FLEET_OIDC_ISSUER","FLEET_OIDC_AUDIENCE","FLEET_OIDC_CLIENT_ID"),sys.argv[1:],strict=True))}))' "$auth0_issuer" "$auth0_audience" "$auth0_client_id")"
k -n fleet rollout status statefulset/postgres --timeout=180s
job="$(k create -k deploy/azure-demo/migration -o name)"
if ! k -n fleet wait --for=condition=complete "$job" --timeout=300s; then
  k -n fleet logs "$job"
  exit 1
fi
k -n fleet rollout restart deployment/api deployment/worker deployment/observations
for resource in deployment/api deployment/web deployment/worker deployment/observations statefulset/kafka; do
  k -n fleet rollout status "$resource" --timeout=180s
done
```

Set the three subject variables from the test users' Auth0 IDs and apply the existing role mapping. This is run from the VM in the same shell as the deploy step.

```bash
: "${proposer_subject:?}" "${approver_subject:?}" "${operator_subject:?}"
k -n fleet exec -i statefulset/postgres -- psql -U fleet -d fleet -v ON_ERROR_STOP=1 \
  -v issuer="$auth0_issuer" -v proposer="$proposer_subject" -v approver="$approver_subject" \
  -v operator="$operator_subject" <<'SQL'
INSERT INTO hosted_identities (issuer, subject, principal_id) VALUES
  (:'issuer', :'proposer', 'demo-partner'),
  (:'issuer', :'approver', 'demo-approver'),
  (:'issuer', :'operator', 'demo-northstar')
ON CONFLICT (issuer, subject) DO UPDATE SET principal_id = excluded.principal_id;
SQL
k -n fleet create secret tls fleet-tls \
  --cert="/etc/letsencrypt/live/$demo_host/fullchain.pem" \
  --key="/etc/letsencrypt/live/$demo_host/privkey.pem" --dry-run=client -o yaml | k apply -f -
k -n fleet create ingress fleet-demo --class=traefik \
  --annotation=traefik.ingress.kubernetes.io/router.entrypoints=websecure \
  --rule="$demo_host/*=web:8080,tls=fleet-tls" --dry-run=client -o yaml | k apply -f -
```

Check `https://$demo_host/api/healthz` for HTTP 200 and `https://$demo_host/api/v1/tenants/northstar/sites/harbor/audit` for HTTP 401 without a token. Confirm the API also disables `POST /api/v1/demo/session` in hosted mode.

## Rehearse Harbor

Use separate browser sessions for the partner proposer, customer approver, and operator. In the Harbor Rehearsal panel, give each plan a fresh ID. Preview both plans as the proposer and approve both revisions as the approver while the topology is healthy.

1. Scale `deployment/worker` to zero and wait for the worker pod to terminate. As the operator, load the first plan and start Wave 1. Run the following twice from the VM to claim and verify the run with seed 1. The override applies only to the one-off worker process inside the API pod.

   ```bash
   k -n fleet exec deployment/api -- env FLEET_DEMO_MODE=local python -m fleet.worker --once
   k -n fleet exec deployment/api -- env FLEET_DEMO_MODE=local python -m fleet.worker --once
   ```

2. Refresh the first run and verify the boot and reachability results. Run `k -n fleet exec deployment/api -- env FLEET_DEMO_MODE=local python -m fleet.scenario peer-down` on the VM. As the operator, load the second approved plan and attempt Wave 1. Expect `alternate_path_unavailable` with an audit ID. Load the audit and capture the blocked plan, role, denial, and browser address bar together.
3. Run `k -n fleet exec deployment/api -- env FLEET_DEMO_MODE=local python -m fleet.scenario healthy`, scale the worker back to one, and wait for its rollout. Each mode override applies to the one-off command.

## Export and publish evidence

In the operator's demo browser session, use the Network panel to identify the access token on a successful request to the demo API. Run the following from a trusted workstation. Paste the token only at the hidden prompt and remove it from your clipboard afterward. Pass the two plan IDs and the Auth0 issuer URL from the deployment.

```bash
read -rs -p 'Demo access token: ' FLEET_EVIDENCE_TOKEN
printf '\n'
export FLEET_EVIDENCE_TOKEN
evidence_file="$(mktemp)"
python3 scripts/export-rehearsal-evidence.py \
  --api-base "https://$demo_host/api" --tenant northstar --site harbor \
  --plan "$passing_plan" --plan "$blocked_plan" \
  --auth0-issuer "$auth0_issuer" --source-commit "$deployed_commit" > "$evidence_file" \
  && mv "$evidence_file" harbor-api-evidence.json
unset FLEET_EVIDENCE_TOKEN
```

The exporter uses only GET requests. It preserves returned event and run fields, limits events to the requested plans, and keeps their audit order. Confirm the healthy plan has one `simulator_started` and its run succeeded. Confirm the blocked plan has `wave_blocked` and no reservation or simulator start. The public claim excerpt contains the issuer and a per-export redaction of the subject from the token accepted by the hosted API. It describes the capture session. Audit actor IDs are the application principals recorded for the earlier actions.

Before publishing, review the JSON and full browser capture for private user names, e-mails, tokens, and internal endpoints. Capture the authenticated app once its URL is free of OAuth callback parameters. Set `deployed_commit` from the checkout used to build the running images. Omit `--source-commit` when the deployed revision was unrecorded. The release workflow captures its own API export from the local Compose browser test under `browser-evidence`.

Deallocate the VM by the end of the approved window. Confirm Azure shows `Stopped (deallocated)`. To end recurring disk and public IP charges, remove the dedicated resource group when the demo is retired.
