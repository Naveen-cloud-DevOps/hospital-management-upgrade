# Hospital Management

A cloud-hosted hospital management system. Staff can register patients, hand out queue tokens, record 5 weeks of
patient progress (with photos), take payments and email receipts. Everything is created and deployed
automatically from GitHub, and it gets a clean web address (Route53 + CloudFront) and a monitoring stack
(Prometheus, Grafana, Loki).

## What it does

- **Sign-up and login** (staff create their own account; the first admin comes from a GitHub secret)
- **Patients**: registration, search, details, 5-week symptom records with photo upload (saved weeks cannot be changed)
- **Welcome email**: every sign-in sends "Welcome to our Hospital Management" to the user's email
- **Doctor appointments**: pick a doctor, date and free time slot; the doctor name, date and time are emailed to the user (cancel/complete also email). Handled by a separate `appointment` service with its **own database** (`hms-appointmentdb` + read replica) and **own S3 bucket** (`hms-<account>-appointments`). Booking status (`booked` / `cancelled` / `completed`) is stored in the `appointments.status` column and as a versioned JSON record `appointments/<id>.json` in that bucket.
- **Queue**: daily tokens with waiting / in consultation / done
- **Payments**: receipts stored in S3, printable, and emailed
- **Admin pages**: weekly report for all patients, user management
- **Languages**: English, Hindi, Telugu
- **Safety**: a database trigger blocks every `DELETE`, so saved data cannot be removed
- **Own web address**: `https://hms.<your-domain>` through Route53 and CloudFront (HTTPS)
- **Monitoring**: CPU/memory dashboards, pod logs (Loki) and CPU alert emails

## Tech stack

| Area | What is used |
|---|---|
| Cloud | AWS (`us-east-1`) |
| Infrastructure as code | Terraform 1.10.5 (state in an S3 bucket) |
| Network | VPC, 2 public + 2 private subnets, NAT gateway |
| Containers | Docker, Amazon ECR |
| Orchestration | Amazon EKS (Kubernetes), managed node group |
| Database | Amazon RDS PostgreSQL 16, one primary + one read replica per service (auth, patient, billing, appointment) |
| Storage | 4 encrypted, versioned S3 buckets: `user-data`, `payment-receipts`, `patient-photos`, `appointments` |
| Email | Amazon SNS (email subscriptions) |
| Pod permissions | IAM role for service accounts (IRSA), no `s3:DeleteObject` on purpose |
| Backend | Python 3.12, FastAPI, 4 services: `auth`, `patient`, `billing`, `appointment` |
| Frontend | One `index.html` (plain HTML, CSS, JavaScript) served by nginx |
| Traffic | ingress-nginx (installed through ArgoCD) in front of the app |
| Web address | Route53 record -> CloudFront (HTTPS, ACM certificate) -> ingress-nginx load balancer |
| Monitoring | Prometheus, Grafana, Alertmanager (email alerts), Loki + Promtail (pod logs), installed with Helm |
| CI/CD | GitHub Actions, Trivy image scanning, ArgoCD (GitOps) |

## How it fits together

```
 push to main
     │
     ▼
 terraform-infra ──► VPC · EKS · RDS · S3 · SNS · IAM        (terraform.yml)
     │ only if successful
     ▼
 cicd ──► build images ─► Trivy scan ─► push to ECR           (docker-build.yml)
      ──► write image names into k8s/ and commit
      ──► install ArgoCD ─► ArgoCD deploys k8s/ to EKS
      ──► prints the app URL and the ArgoCD URL + password
     │ only if successful, these two start at the same time
     ├──────────────────────────────┐
     ▼                              ▼
 edge-cloudfront                 monitoring
 (edge-cloudfront.yml)           (monitoring.yml)
 finds the load balancer,        installs Prometheus, Grafana,
 applies terraform/dns.tf:       Alertmanager and Loki,
 ACM + CloudFront + Route53,     prints the Grafana URL, username,
 prints https://hms.<domain>     password and the Prometheus URL

 manual only:  monitoring-tools (monitoring-tools.yml)
               reset the Grafana password / test the CPU email alert

 Browser ──► Route53 ──► CloudFront ──► load balancer ──► ingress-nginx ──┬─ /auth      → auth-service
                                                                          ├─ /patients  → patient-service
                                                                          ├─ /queue     → patient-service
                                                                          ├─ /payments  → billing-service
                                                                          └─ /          → frontend
```

## Repository layout

```
.
├── .github/workflows/
│   ├── terraform.yml          # workflow "terraform-infra": builds the AWS infrastructure
│   ├── docker-build.yml       # workflow "cicd": images, scan, deploy (runs after terraform-infra)
│   ├── edge-cloudfront.yml    # CloudFront + Route53 (runs after cicd), prints the app URL
│   ├── monitoring.yml         # Prometheus + Grafana + Loki (runs after cicd), prints the Grafana login
│   ├── monitoring-tools.yml   # manual: reset Grafana password, CPU alert test
│   └── debug-cluster.yml      # manual: cluster checks
├── terraform/                 # main.tf, rds.tf, dns.tf, variables.tf, outputs.tf, terraform.tfvars
├── services/
│   ├── Dockerfile             # one Dockerfile for all 4 services (build arg SERVICE)
│   ├── requirements.txt       # Python dependencies (keep these patched, Trivy fails the build otherwise)
│   ├── common.py              # shared DB, JWT, S3 and SNS helpers
│   ├── auth/main.py           # login, sign-up, users, email test
│   ├── patient/main.py        # patients, weekly records, queue, email details
│   ├── billing/main.py        # payments, receipts, receipt email
│   └── appointment/main.py    # doctors, bookings, booking status (own DB + S3 bucket)
├── frontend/
│   ├── Dockerfile             # nginx-unprivileged + index.html
│   └── index.html             # the whole user interface
├── k8s/                       # deployments, services, ingress, config
├── monitoring/                # Helm values, Loki log dashboard, monitoring README
└── argocd/application.yaml    # ArgoCD apps: ingress-nginx and hospital-management
```

## Before you deploy

You need:

- An **AWS account** that allows EKS, RDS, EC2, S3, SNS, IAM, CloudFront, ACM and Route53. Sandbox accounts often block some of these or limit EC2 sizes.
- An **IAM user** with an access key (AdministratorAccess is the easiest for a test project).
- A **Route53 public hosted zone** for your domain (for example `855455101846.realhandsonlabs.net`).
- A **GitHub repository** with this code on the `main` branch.
- If the repository is **private**, ArgoCD needs credentials to read it. A public repository works without extra setup.

### 1. Add GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Required | Value |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Yes | Access key of your IAM user |
| `AWS_SECRET_ACCESS_KEY` | Yes | Secret key of your IAM user |
| `AWS_REGION` | No | AWS region to deploy to, for example `us-east-1` (default) |
| `DB_USERNAME` | Yes | Database master username, for example `hmsadmin` (no `admin`, it is reserved) |
| `DB_PASSWORD` | Yes | Database master password (8+ characters, no `/`, `@`, `"` or spaces) |
| `JWT_SECRET` | Yes | A long random string used to sign login tokens |
| `ADMIN_EMAIL` | Yes | Email of the first admin account |
| `ADMIN_PASSWORD` | Yes | Password of the first admin account |
| `TFVARS` | Yes for the web address | Full contents of `terraform.tfvars`, including `hosted_zone_name` (see step 2). It replaces the file in the repo on every run |
| `ALERT_SMTP_PASSWORD` | Yes for monitoring | Gmail **app password** used by Alertmanager to send CPU alert emails |
| `PROMETHEUS_ALLOWED_CIDR` | No | e.g. `203.0.113.10/32`, limits who can open Prometheus (it has no login) |
| `GH_USERNAME` | No | Your GitHub username (defaults to the repository owner) |
| `GH_PAT` | yes | A personal access token, used instead of the built-in `GITHUB_TOKEN` to push manifests |
| `HOSTED_ZONE_NAME` | No | Only if you do not put `hosted_zone_name` in `TFVARS` |

`GITHUB_TOKEN` is created by GitHub automatically; you cannot add it as a secret.
Also set **Settings → Actions → General → Workflow permissions** to **Read and write**.

The Grafana username and password are **not** secrets: they are written at the top of `.github/workflows/monitoring.yml` (`GRAFANA_USER`, `GRAFANA_PASSWORD`).

### 2. Check `terraform/terraform.tfvars` (or the `TFVARS` secret)

```hcl
region              = "******"
project             = "*****"
cluster_name        = "******"
eks_version         = "1.31"          # see "EKS version" below
node_instance_types = ["******"]    # use a smaller type if your account blocks this one
node_min_size       = 2
node_max_size       = 4
node_desired_size   = 3

# web address (used by terraform/dns.tf)
hosted_zone_name = "***********"   # your Route53 public hosted zone
app_subdomain    = "ritikhms"                                # gives https://hms.855455101846.realhandsonlabs.net
```

Keep `hosted_zone_name` in the file or secret permanently. If it is removed later, the next run would remove CloudFront and the Route53 record.
If you change `region`, `project` or `cluster_name`, also update the workflows and the files in `k8s/`.

## Deploy

1. Commit and push everything to `main`.
2. Open the **Actions** tab. The workflows run one after the other:
   1. **terraform-infra** creates the S3 state bucket and all AWS resources. This takes **25 to 40 minutes** the first time (EKS and RDS are slow).
   2. **cicd** starts by itself when the first one succeeds. It builds the images, scans them, pushes them to ECR, installs ArgoCD and deploys the app.
   3. **edge-cloudfront** and **monitoring** start by themselves when **cicd** succeeds.
3. Read the **Summary** page of each finished run:
   - **cicd**: ArgoCD URL, username (`admin`) and password, plus the plain load balancer URL.
   - **edge-cloudfront**: the **application URL**, `https://hms.<your-domain>`. Log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`. The first run takes about 5 to 10 minutes because CloudFront and the certificate are slow.
   - **monitoring**: Grafana URL, username and password, and the Prometheus URL.

You can also start any workflow by hand from the **Actions** tab with **Run workflow**.

To release a change later, just push to `main`. Only changes to `terraform/`, `services/`, `frontend/` or the workflow files start a new run.

## Using the app

- **Admin:** sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Admins see the Weekly report and Users pages and can create other admins.
- **Staff:** anyone can use **Create account** on the login page. New accounts get staff access only.
- **Email:** emails go out through Amazon SNS. Each address must confirm once by clicking **Confirm subscription** in the message AWS sends. Until then no emails arrive. The **Account** page has **Resend email** and **Send test email** buttons.

## Monitoring

Installed by `monitoring.yml` after every successful deploy. Full guide: [monitoring/README.md](monitoring/README.md).

- **Grafana**: dashboards for node and pod CPU, memory and network, plus **Pod Logs (Loki)**.
- **Logs**: Grafana → **Explore** → data source **Loki**, one query at a time, for example `{namespace="hms"}` (app logs) or `{namespace=~".+"}` (everything).
- **Alerts**: `NodeHighCPU` (node above 80% for 5 min) and `PodHighCPU` (pod above 0.8 core for 5 min) are emailed through Alertmanager.
- **Manual tools**: Actions → **monitoring-tools** → Run workflow → choose `reset-grafana-password`, `test-cpu-alert` or `delete-cpu-test`.

## API overview

| Method and path | Who | Purpose |
|---|---|---|
| `POST /auth/signup` | public | Create a staff account |
| `POST /auth/login` | public | Get a login token |
| `POST /auth/register` | admin | Create a user with any role |
| `GET /auth/users` | admin | List users |
| `POST /auth/notifications/resend` / `test` | signed in | Re-send the AWS confirmation email / send a test email |
| `GET/POST /patients`, `GET /patients/{id}` | signed in | List, add, view patients |
| `POST /patients/{id}/weeks` | signed in | Save week 1 to 5 (symptoms and photo) |
| `POST /patients/{id}/token`, `GET /queue`, `PATCH /queue/{id}` | signed in | Queue |
| `POST /patients/{id}/email` | signed in | Email patient details |
| `GET /patients/weekly-report` | signed in | All patients' weekly records |
| `POST/GET /payments`, `GET /payments/{id}/receipt`, `POST /payments/{id}/email` | signed in | Payments and receipts |

## Useful commands

Connect to the cluster (use the same AWS account as the pipeline):

```bash
aws eks update-kubeconfig --name hms-eks --region us-east-1
kubectl -n hms get pods
kubectl -n hms logs deploy/auth-service
kubectl -n argocd get applications
kubectl -n ingress-nginx get svc
kubectl -n monitoring get pods
```

Get the ArgoCD password later:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

## Troubleshooting

### 1. Grafana: "Login failed - Invalid username or password"

**Cause:** the old `monitoring.yml` printed the password with a space between each character (to get around GitHub hiding secrets), and the spaces were typed in as part of the password.

**Fix:**
1. Type the password with no spaces.
2. The login is now written at the top of `.github/workflows/monitoring.yml` (`GRAFANA_USER` and `GRAFANA_PASSWORD`), and the monitoring run prints it in plain text.
3. If it still fails, Grafana kept an old password in its database. Run **Actions → monitoring-tools → Run workflow → `reset-grafana-password`**, or re-run **monitoring**.

### 2. Loki (Grafana Explore): "parse error at line 5, col 1: syntax error: unexpected {"

**Cause:** several queries were pasted into one query box. Loki runs only one query at a time.

**Fix:**
1. Click in the box, press **Ctrl+A**, then **Delete**.
2. Paste one query only, for example `{namespace="hms"}`.
3. Run it with **Shift+Enter** or the blue refresh button (set the time range to **Last 1 hour**).
4. To run more than one query, click **Add query** and put one query in each box.

### 3. Pod status "CrashLoopBackOff"

**Cause:** the container starts, crashes and restarts again and again. The usual reason is a wrong database username or password, or a database the pod cannot reach.

**Fix:**
1. Read the reason: `kubectl -n hms logs <pod> --previous` (find the pod name with `kubectl -n hms get pods`).
2. If the log shows a database or authentication error, correct `DB_USERNAME` and `DB_PASSWORD` (see problem 4).
3. Run **terraform-infra** and then **cicd** again so the pods get the right credentials.
4. Restart the pods: `kubectl -n hms rollout restart deploy`.
5. Also check that the secret `JWT_SECRET` is set and that the `*db-conn` secrets exist: `kubectl -n hms get secret`.

### 4. terraform-infra fails: wrong database credentials

**Cause:** RDS refuses the master username or password.

**Fix:**
1. The username must not be `admin` (it is reserved). Use something like `hmsadmin`.
2. The password must be 8 or more characters and must not contain `/`, `@`, `"` or a space.
3. Correct the `DB_USERNAME` and `DB_PASSWORD` secrets exactly, with no space at the end.
4. Re-run **terraform-infra**. If the database already exists, the new password is applied on the next successful run.
5. Run **cicd** after it, so the pods get the new credentials (see problem 3).

## Cost and clean-up

This setup is not free: the EKS control plane, worker nodes, a NAT gateway, six RDS instances, CloudFront and the load balancers
(app, ArgoCD, Grafana, Prometheus) all cost money while they run. Destroy everything when you are done:

1. Delete the load balancers first, or the VPC cannot be removed:
   ```bash
   kubectl -n ingress-nginx delete svc ingress-nginx-controller
   kubectl -n argocd delete svc argocd-server
   helm -n monitoring uninstall kube-prometheus-stack loki
   ```
2. In `terraform.tfvars` set `db_deletion_protection = false` and apply it once (push to `main`).
3. Run `terraform destroy` from the `terraform/` folder, with the same backend settings as the workflow. This also removes CloudFront, the certificate and the Route53 records created by `dns.tf`.
4. Delete by hand what Terraform does not manage: the four ECR repositories (`hospital-management-*`) and the state bucket `hms-tfstate-<account-id>`.

## Security notes

- Never commit real secrets. Use GitHub secrets only.
- The pipeline prints the ArgoCD admin password in the run summary, and `monitoring.yml` prints the Grafana password. In a public repository anyone can read them, so make the repository private, or change the passwords after the first login.
- The Grafana password is written in `monitoring.yml` in plain text. Change `GRAFANA_PASSWORD` to your own value and keep the repository private.
- Prometheus has no login. Set the `PROMETHEUS_ALLOWED_CIDR` secret to limit who can open it.
- Self sign-up gives anyone with the URL staff access to patient records. For real use, add an admin approval step.
- The Kubernetes API is public (`cluster_endpoint_public_access = true`) so GitHub runners can deploy. Restrict it for production.
