"""Read-only: dump current allowed_models for BedrockUser-cgjang and check
whether the steering-mandated models + their pricing rows exist."""
import boto3, json

PROFILE = "bedrock-gw"
REGION = "us-west-2"
POLICY_TABLE = "bedrock-gw-dev-us-west-2-principal-policy"
PRICING_TABLE = "bedrock-gw-dev-us-west-2-model-pricing"
PID = "107650139384#BedrockUser-cgjang"

MANDATED = [
    "anthropic.claude-3-opus-20240229-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
]

s = boto3.Session(profile_name=PROFILE, region_name=REGION)
ddb = s.resource("dynamodb")

pol = ddb.Table(POLICY_TABLE)
item = pol.get_item(Key={"principal_id": PID}).get("Item")
if not item:
    print(f"!! principal row NOT FOUND: {PID}")
else:
    allowed = item.get("allowed_models", [])
    print(f"principal: {PID}")
    print(f"allowed_models count: {len(allowed)}")
    print("sample (first 20):")
    for m in list(allowed)[:20]:
        print("   ", m)
    print("\nmandated-model presence:")
    for m in MANDATED:
        print(f"   {'YES' if m in allowed else 'NO '}  {m}   (us. variant: {'YES' if 'us.'+m in allowed else 'NO'})")

pricing = ddb.Table(PRICING_TABLE)
print("\npricing rows:")
for m in MANDATED:
    for cand in (m, "us." + m):
        row = pricing.get_item(Key={"model_id": cand}).get("Item")
        print(f"   {'HAS' if row else 'MISS'}  {cand}")
