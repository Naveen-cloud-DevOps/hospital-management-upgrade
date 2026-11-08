# Monitoring (Prometheus + Grafana + Alertmanager + Loki)

Deployed by `.github/workflows/monitoring.yml`, which starts automatically when the `cicd` workflow finishes successfully.

## Add these GitHub secrets (Settings > Secrets and variables > Actions)

| Secret | Required | Value |
|---|---|---|
| `GRAFANA_ADMIN_PASSWORD` | Yes | Grafana login password |
| `ALERT_SMTP_PASSWORD` | Yes | Gmail **app password** for ritikranjanspiderhacker@gmail.com (Google account > Security > 2-Step Verification > App passwords) |
| `GRAFANA_ADMIN_USER` | No | Defaults to `ritik` |
| `PROMETHEUS_ALLOWED_CIDR` | No | e.g. `203.0.113.10/32`, limits who can open Prometheus (it has no login) |

## URLs
Open the finished **monitoring** run > Summary. It shows the Grafana URL (`http://<elb-host>`) and the Prometheus URL (`http://<elb-host>:9090`).
Or: `kubectl -n monitoring get svc kube-prometheus-stack-grafana kube-prometheus-stack-prometheus`

## What you get
- Grafana > Dashboards: node and pod CPU/memory/network dashboards (built in), plus **Pod Logs (Loki)** for the logs of every pod.
- Alerts `NodeHighCPU` (node above 80% for 5 min) and `PodHighCPU` (pod above 0.8 core for 5 min) are emailed. Edit the numbers in `kube-prometheus-stack-values.yaml`.

## Test the email alert
```bash
kubectl run cpu-test --image=busybox --restart=Never -- sh -c 'while true; do :; done'
# wait about 6-7 minutes, check Alertmanager/mail, then:
kubectl delete pod cpu-test
```