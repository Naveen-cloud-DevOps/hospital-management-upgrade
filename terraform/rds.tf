# One RDS PostgreSQL per service (auth / patient / billing / appointment / doctor / laboratory / pharmacy), each with its own read replica.
# primary = writes, replica = reads (RDS keeps them in sync automatically).
# appointment / doctor / laboratory / pharmacy are always included, even if an old TFVARS secret / tfvars file does not list them
locals { dbs = toset(concat(var.db_services, ["appointment", "doctor", "laboratory", "pharmacy"])) }

resource "aws_db_subnet_group" "rds" {
  name       = "${var.project}-rds"
  subnet_ids = module.vpc.private_subnets

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "rds" {
  name   = "${var.project}-rds"
  vpc_id = module.vpc.vpc_id

  ingress { # only the EKS nodes/pods can reach the databases
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id, module.eks.cluster_primary_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "primary" {
  for_each                  = local.dbs
  identifier                = "${var.project}-${each.key}db"
  engine                    = "postgres"
  engine_version            = var.db_engine_version
  instance_class            = var.db_instance_class
  allocated_storage         = var.db_allocated_storage
  storage_type              = var.db_storage_type
  storage_encrypted         = true
  db_name                   = "app"
  username                  = var.db_username # from GitHub secret DB_USERNAME
  password                  = var.db_password # from GitHub secret DB_PASSWORD
  db_subnet_group_name      = aws_db_subnet_group.rds.name
  vpc_security_group_ids    = [aws_security_group.rds.id]
  publicly_accessible       = false
  backup_retention_period   = var.db_backup_retention_days # required for read replicas
  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project}-${each.key}db-final"
  apply_immediately         = true

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "replica" {
  for_each               = local.dbs
  identifier             = "${var.project}-${each.key}db-replica"
  replicate_source_db    = aws_db_instance.primary[each.key].arn # must be the ARN, not the identifier
  instance_class         = var.db_replica_instance_class
  storage_encrypted      = true
  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  skip_final_snapshot    = true

  lifecycle {
    create_before_destroy = true
  }
}