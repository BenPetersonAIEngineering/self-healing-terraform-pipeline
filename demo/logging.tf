# Access logging for the logs bucket, so access to it is itself auditable.
resource "aws_s3_bucket_logging" "logs" {
  bucket = aws_s3_bucket.logs.id

  target_bucket = aws_s3_bucket.logz.id
  target_prefix = "log/"
}
