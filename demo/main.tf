terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Points at LocalStack, not real AWS — see ../.github/workflows/terraform-demo.yml.
# This is deliberately a small, real module (not a toy single resource) so it can
# host a realistic bug for the self-healer's live-trigger integration to fix.
provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style            = true
  skip_credentials_validation  = true
  skip_metadata_api_check      = true
  skip_requesting_account_id   = true

  endpoints {
    s3 = "http://localhost:4566"
  }
}

resource "aws_s3_bucket" "logs" {
  bucket = "self-healer-demo-logs"
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enable"
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket = aws_s3_bucket.logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
