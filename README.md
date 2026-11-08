# Hospital Management

A cloud-hosted hospital management system. Staff can register patients, hand out queue tokens, record 5 weeks of
patient progress (with photos), take payments and email receipts. Everything is created and deployed
automatically from GitHub.

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
| CI/CD | GitHub Actions, Trivy image scanning, ArgoCD (GitOps) |

## How it fits together

```
 push to main
     │
     ▼
 terraform-infra ──► VPC · EKS · RDS · S3 · SNS · IAM        (workflow: terraform.yml)
     │ only if successful
     ▼
 cicd ──► build images ─► Trivy scan ─► push to ECR
      ──► write image names into k8s/ and commit
      ──► install ArgoCD ─► ArgoCD deploys k8s/ to EKS
      ──► prints the app URL and the ArgoCD URL + password   (workflow: cicd.yml)

 Browser ──► load balancer ──► ingress-nginx ──┬─ /auth      → auth-service
                                               ├─ /patients  → patient-service
                                               ├─ /queue     → patient-service
                                               ├─ /payments  → billing-service
                                               └─ /          → frontend
```

## Repository layout

```
.
├── .github/workflows/
│   ├── terraform.yml        # workflow "terraform-infra": builds the AWS infrastructure
│   └── cicd.yml             # workflow "cicd": images, scan, deploy (runs after terraform-infra)
├── terraform/               # main.tf, rds.tf, variables.tf, outputs.tf, terraform.tfvars
├── services/
│   ├── Dockerfile           # one Dockerfile for all 3 services (build arg SERVICE)
│   ├── requirements.txt     # Python dependencies (keep these patched, Trivy fails the build otherwise)
│   ├── common.py            # shared DB, JWT, S3 and SNS helpers
│   ├── auth/main.py         # login, sign-up, users, email test
│   ├── patient/main.py      # patients, weekly records, queue, email details
│   ├── billing/main.py      # payments, receipts, receipt email
│   └── appointment/main.py  # doctors, bookings, booking status (own DB + S3 bucket)
├── frontend/
│   ├── Dockerfile           # nginx-unprivileged + index.html
│   └── index.html           # the whole user interface
├── k8s/                     # deployments, services, ingress, config
└── argocd/application.yaml  # ArgoCD apps: ingress-nginx and hospital-management
```

## Before you deploy

You need:

- An **AWS account** that allows EKS, RDS, EC2, S3, SNS and IAM. Sandbox accounts often block some of these or limit EC2 sizes.
- An **IAM user** with an access key (AdministratorAccess is the easiest for a test project).
- A **GitHub repository** with this code on the `main` branch.
- If the repository is **private**, ArgoCD needs credentials to read it. A public repository works without extra setup.

### 1. Add GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Required | Value |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Yes | Access key of your IAM user |
| `AWS_SECRET_ACCESS_KEY` | Yes | Secret key of your IAM user |
| `AWS_REGION` | No | AWS region to deploy to, for example `us-east-1` (default) or `ap-south-1` |
| `DB_USERNAME` | Yes | Database master username, for example `hmsadmin` (no `admin`, it is reserved) |
| `DB_PASSWORD` | Yes | Database master password (8+ characters, no `/`, `@`, `"` or spaces) |
| `JWT_SECRET` | Yes | A long random string used to sign login tokens |
| `ADMIN_EMAIL` | Yes | Email of the first admin account |
| `ADMIN_PASSWORD` | Yes | Password of the first admin account |
| `TFVARS` | No | Full contents of `terraform.tfvars`. If set, it replaces the file in the repo on every run |
| `GH_USERNAME` | No | Your GitHub username (defaults to the repository owner) |
| `GH_PAT` | No | A personal access token, used instead of the built-in `GITHUB_TOKEN` to push manifests |

`GITHUB_TOKEN` is created by GitHub automatically; you cannot add it as a secret.
Also set **Settings → Actions → General → Workflow permissions** to **Read and write**.

### 2. Check `terraform/terraform.tfvars`

The main values to review:

```hcl
region              = "us-east-1"
project             = "hms"
cluster_name        = "hms-eks"
eks_version         = "1.35"          # see "EKS version" below
node_instance_types = ["t3.large"]    # use a smaller type if your account blocks this one
node_min_size       = 2
node_max_size       = 4
node_desired_size   = 3
```

If you change `region`, `project` or `cluster_name`, also update the workflows and the files in `k8s/`.

## Deploy

1. Commit and push everything to `main`.
2. Open the **Actions** tab. Two workflows run one after the other:
   1. **terraform-infra** creates the S3 state bucket and all AWS resources. This takes **25 to 40 minutes** the first time (EKS and RDS are slow).
   2. **cicd** starts by itself when the first one succeeds. It builds the images, scans them, pushes them to ECR, installs ArgoCD and deploys the app.
3. When **cicd** finishes, open the run and read the **Summary** page. It shows:
   - **Application URL**: log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.
   - **ArgoCD URL, username (`admin`) and password**.

You can also start either workflow by hand from the **Actions** tab with **Run workflow**.

To release a change later, just push to `main`. Only changes to `terraform/`, `services/`, `frontend/` or the workflow files start a new run.

## Using the app

- **Admin:** sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Admins see the Weekly report and Users pages and can create other admins.
- **Staff:** anyone can use **Create account** on the login page. New accounts get staff access only.
- **Email:** emails go out through Amazon SNS. Each address must confirm once by clicking **Confirm subscription** in the message AWS sends. Until then no emails arrive. The **Account** page has **Resend email** and **Send test email** buttons.

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
```

Get the ArgoCD password later:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `Unsupported Kubernetes minor version update from 1.30 to 1.35` | EKS upgrades one minor version at a time. Step `eks_version` through 1.31, 1.32 and so on, one pipeline run each, or recreate the cluster at the version you want. |
| `You are not authorized to launch instances with this launch template` | The IAM user or the sandbox blocks EC2 launches. Attach `AdministratorAccess`, then try a smaller `node_instance_types` such as `["t3.small"]` (also in the `TFVARS` secret). |
| `Unable to resolve action aquasecurity/trivy-action@...` | The tag no longer exists. Use `aquasecurity/trivy-action@0.35.0` or newer in `cicd.yml`, and make sure only one workflow file uses it. |
| Trivy scan fails with HIGH or CRITICAL findings | Update the packages named in the report in `services/requirements.txt`, or rebuild the frontend image with `apk upgrade` as in `frontend/Dockerfile`. Do not switch the scan off. |
| App URL shows `404 Not Found nginx` | ingress-nginx is running but the app is not deployed. Run `kubectl -n argocd get applications`. The usual cause is a wrong `repoURL` in `argocd/application.yaml` or a private repository. |
| Two workflows both named `cicd` | An old `docker-build.yml` is still in `.github/workflows/`. Delete it. |
| No emails arrive | The address has not confirmed its SNS subscription. See "Using the app", or check the topic `hms-notifications` in the SNS console. |
| `terraform.tfvars` changes are ignored | The `TFVARS` secret overwrites the file on every run. Edit the secret too. |

## Cost and clean-up

This setup is not free: the EKS control plane, worker nodes, a NAT gateway, six RDS instances and two load balancers
(app and ArgoCD) all cost money while they run. Destroy everything when you are done:

1. Delete the load balancers first, or the VPC cannot be removed:
   ```bash
   kubectl -n ingress-nginx delete svc ingress-nginx-controller
   kubectl -n argocd delete svc argocd-server
   ```
2. In `terraform.tfvars` set `db_deletion_protection = false` and apply it once (push to `main`).
3. Run `terraform destroy` from the `terraform/` folder, with the same backend settings as the workflow.
4. Delete by hand what Terraform does not manage: the four ECR repositories (`hospital-management-*`) and the state bucket `hms-tfstate-<account-id>`.

## Security notes

- Never commit real secrets. Use GitHub secrets only.
- The pipeline prints the ArgoCD admin password in the run summary. In a public repository anyone can read it, so make the repository private or change that password and remove the lines from `cicd.yml` after the first deploy.
- Self sign-up gives anyone with the URL staff access to patient records. For real use, add an admin approval step.
- The Kubernetes API is public (`cluster_endpoint_public_access = true`) so GitHub runners can deploy. Restrict it for production.
