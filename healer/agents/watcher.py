"""Watcher: structures raw error output. No repo access — text in, text out.

Real LLM call as of slice 2.
"""
from healer import llm
from healer.json_util import parse_json_response
from healer.models import StructuredError

SYSTEM_PROMPT = """You are the Watcher agent in a self-healing Terraform CI/CD pipeline.
You structure raw `terraform apply` error output into a fixed schema. You have no
repo access and see only the error text below — do not invent details not present in it.

Respond with ONLY a JSON object of this exact shape:
{
  "resource_address": "<the Terraform resource address the error is about, e.g. aws_instance.web, or null>",
  "error_class": "<a short category, e.g. InvalidParameterValue, AccessDenied, CyclicDependency, Unknown>",
  "raw_excerpt": "<the most relevant excerpt of the error, at most 500 characters>",
  "aws_service": "<the AWS service involved, e.g. ec2, s3, iam, or null>"
}"""


def structure_error(raw_output: str) -> StructuredError:
    response = llm.complete(SYSTEM_PROMPT, raw_output)
    data = parse_json_response(response)
    return StructuredError(
        resource_address=data.get("resource_address"),
        error_class=data.get("error_class", "Unknown"),
        raw_excerpt=data.get("raw_excerpt", raw_output.strip()[:500]),
        aws_service=data.get("aws_service"),
    )
