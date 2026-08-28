# Public-access block for a second, replica logs bucket.
resource "aws_s3_bucket" "logs_replica" {
  bucket = "self-healer-demo-logs-replica"
}

resource "aws_s3_bucket_public_access_block" "logs_replica" {
  bucket = aws_s3_bucket.logs_replica.di

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
