# Monitoring (Prometheus + Grafana + Alertmanager + Loki)

Deployed by `.github/workflows/monitoring.yml`, which starts automatically when the `cicd` workflow finishes successfully (or run it by hand: Actions > monitoring > Run workflow).

## Grafana login (set in the workflow, no secret)

The Grafana username and password are written at the top of `.github/workflows/monitoring.yml`:

```yaml
env:
  GRAFANA_USER: *****
  GRAFANA_PASSWORD: *****   # change it; avoid  "  \  and spaces
```

Change them there and push. The workflow prints the URL, username and password at the end of the run.
The password is visible to anyone who can read the repo or its Actions logs, so keep the repo private.

## GitHub secrets (Settings > Secrets and variables > Actions)

| Secret | Required | Value |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Yes | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS secret key |
| `AWS_REGION` | No | Defaults to `us-east-1` |
| `ALERT_SMTP_PASSWORD` | Yes | Gmail **app password** for ritikranjanspiderhacker@gmail.com (Google account > Security > 2-Step Verification > App passwords) |
| `PROMETHEUS_ALLOWED_CIDR` | No | e.g. `203.0.113.10/32`, limits who can open Prometheus (it has no login) |

`GRAFANA_ADMIN_PASSWORD` and `GRAFANA_ADMIN_USER` are no longer used and can be deleted.

## URLs

Open the finished **monitoring** run: the log and the **Summary** page show

```
GRAFANA URL     : http://<elb-host>
GRAFANA USERNAME: *****
GRAFANA PASSWORD: *****
PROMETHEUS URL  : http://<elb-host>:9090
```

Or: `kubectl -n monitoring get svc kube-prometheus-stack-grafana kube-prometheus-stack-prometheus`

## What you get

- Grafana > Dashboards: node and pod CPU/memory/network dashboards (built in), plus **Pod Logs (Loki)** for the logs of every pod.
- Alerts `NodeHighCPU` (node above 80% for 5 min) and `PodHighCPU` (pod above 0.8 core for 5 min) are emailed. Edit the numbers in `kube-prometheus-stack-values.yaml`.

## Check logs with Loki

### Easy way: dashboard
Grafana > Dashboards > **Pod Logs (Loki)**. Pick the **Namespace** (`hms` for the app) and **Pod** at the top, and type a word in **Search text** to filter. Refreshes every 10 s.

### Explore (your own queries)
1. Grafana > **Explore** > choose the **Loki** data source > switch **Builder** to **Code**.
2. Put **one query only** in the box (Ctrl+A, Delete first). Several queries in one box give `parse error ... unexpected {`. For more than one, click **Add query**.
3. Run with the blue refresh button or **Shift+Enter**. Set the time range (clock icon) to **Last 1 hour**.

| What | Query |
|---|---|
| All logs, every namespace | `{namespace=~".+"}` |
| All app logs | `{namespace="hms"}` |
| One service | `{namespace="hms", pod=~"auth-service.*"}` |
| One exact pod | `{namespace="hms", pod="<pod-name>"}` |
| Only errors | `{namespace="hms"} \|~ "(?i)error\|exception\|traceback"` |
| Hide health checks | `{namespace="hms"} != "/health"` |
| Ingress / load balancer | `{namespace="ingress-nginx"}` |
| Everything on one node | `{node_name="<node-name>"}` |

Services: `auth-service`, `patient-service`, `billing-service`, `appointment-service`.
Find pod names with `kubectl get pods -n hms` and node names with `kubectl get nodes`.
If `node_name` is not found, open **Label browser** in Explore to see the label names.

Notes:
- The screen shows 1000 lines by default; raise **Line limit** under **Options**.
- Loki keeps logs in the pod only (no disk), so logs older than the last Loki restart are lost.
- Only pod/container logs are collected, not the node's own system logs (kubelet, OS journal).

## If Grafana says "Invalid username or password"

Type the password exactly as written in the workflow (no spaces). If it still fails, Grafana kept an old password in its database; the workflow resets it on every run, so re-run **monitoring**, or run:

```bash
kubectl -n monitoring exec deploy/kube-prometheus-stack-grafana -c grafana -- grafana cli admin reset-admin-password '<password>'
```

## Test the email alert

```bash
kubectl run cpu-test --image=busybox --restart=Never -- sh -c 'while true; do :; done'
# wait about 6-7 minutes, check Alertmanager/mail, then:
kubectl delete pod cpu-test
```
