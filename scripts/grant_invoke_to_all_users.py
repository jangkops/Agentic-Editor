#!/usr/bin/env python3
"""Grant AllowDevGatewayInvoke inline policy to ALL BedrockUser-* roles.

This enables every user to call Bedrock InvokeModel through the /invoke
gateway route — used for Titan Embed, Cohere Embed/Rerank, Stability image
generation, and any other model that requires the Invoke API instead of
Converse.

Idempotent — re-running just refreshes the policy.
"""
import boto3
import json
import sys

PROFILE = "bedrock-gw"
ROLE_PREFIX = "BedrockUser-"
POLICY_NAME = "AllowDevGatewayInvoke"
INVOKE_ARN = "arn:aws:execute-api:us-west-2:107650139384:5l764dh7y9/v1/POST/invoke"

POLICY_DOC = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "execute-api:Invoke",
            "Resource": [INVOKE_ARN],
        }
    ],
}


def main():
    session = boto3.Session(profile_name=PROFILE)
    iam = session.client("iam")

    # List all BedrockUser roles
    roles = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page["Roles"]:
            if role["RoleName"].startswith(ROLE_PREFIX):
                roles.append(role["RoleName"])

    print(f"Found {len(roles)} BedrockUser roles")

    success = 0
    failed = 0

    for role_name in sorted(roles):
        try:
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=POLICY_NAME,
                PolicyDocument=json.dumps(POLICY_DOC),
            )
            print(f"  [OK] {role_name}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {role_name}: {str(e)[:120]}")
            failed += 1

    print(f"\nDone. Success: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
