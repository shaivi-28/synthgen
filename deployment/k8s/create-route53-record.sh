#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-ap-south-1}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-Z0131176AB0AGZHNO6UT}"
ALB_NAME="${ALB_NAME:-eks-tkzn-staging-lb}"
RECORD_NAME="${RECORD_NAME:-synthgen.staging.osfin.ai}"

RECORD_FQDN="${RECORD_NAME%.}."

echo "Checking ALB ${ALB_NAME} in ${REGION}..."
ALB_DNS="$(aws elbv2 describe-load-balancers \
  --region "${REGION}" \
  --names "${ALB_NAME}" \
  --query 'LoadBalancers[0].DNSName' \
  --output text)"

ALB_ZONE_ID="$(aws elbv2 describe-load-balancers \
  --region "${REGION}" \
  --names "${ALB_NAME}" \
  --query 'LoadBalancers[0].CanonicalHostedZoneId' \
  --output text)"

if [[ -z "${ALB_DNS}" || "${ALB_DNS}" == "None" || -z "${ALB_ZONE_ID}" || "${ALB_ZONE_ID}" == "None" ]]; then
  echo "Could not find ALB details for ${ALB_NAME}."
  exit 1
fi

echo "Checking Route 53 record ${RECORD_FQDN}..."
EXISTING_RECORD_NAME="$(aws route53 list-resource-record-sets \
  --hosted-zone-id "${HOSTED_ZONE_ID}" \
  --start-record-name "${RECORD_FQDN}" \
  --start-record-type A \
  --max-items 1 \
  --query 'ResourceRecordSets[0].Name' \
  --output text)"

EXISTING_RECORD_TYPE="$(aws route53 list-resource-record-sets \
  --hosted-zone-id "${HOSTED_ZONE_ID}" \
  --start-record-name "${RECORD_FQDN}" \
  --start-record-type A \
  --max-items 1 \
  --query 'ResourceRecordSets[0].Type' \
  --output text)"

EXISTING_ALIAS_DNS="$(aws route53 list-resource-record-sets \
  --hosted-zone-id "${HOSTED_ZONE_ID}" \
  --start-record-name "${RECORD_FQDN}" \
  --start-record-type A \
  --max-items 1 \
  --query 'ResourceRecordSets[0].AliasTarget.DNSName' \
  --output text)"

EXISTING_ALIAS_ZONE_ID="$(aws route53 list-resource-record-sets \
  --hosted-zone-id "${HOSTED_ZONE_ID}" \
  --start-record-name "${RECORD_FQDN}" \
  --start-record-type A \
  --max-items 1 \
  --query 'ResourceRecordSets[0].AliasTarget.HostedZoneId' \
  --output text)"

if [[ "${EXISTING_RECORD_NAME}" == "${RECORD_FQDN}" && "${EXISTING_RECORD_TYPE}" == "A" ]]; then
  if [[ "${EXISTING_ALIAS_DNS}" == "None" ]]; then
    echo "Route 53 A record already exists for ${RECORD_FQDN}, but it is not an alias record."
    echo "Not changing the existing record."
    exit 1
  fi

  EXISTING_ALIAS_DNS_NORMALIZED="${EXISTING_ALIAS_DNS%.}"
  ALB_DNS_NORMALIZED="${ALB_DNS%.}"

  if [[ "${EXISTING_ALIAS_DNS_NORMALIZED}" == "${ALB_DNS_NORMALIZED}" && "${EXISTING_ALIAS_ZONE_ID}" == "${ALB_ZONE_ID}" ]]; then
    echo "Route 53 record already exists: ${RECORD_FQDN} -> ${EXISTING_ALIAS_DNS}"
    exit 0
  fi

  echo "Route 53 A record already exists for ${RECORD_FQDN}, but points somewhere else:"
  echo "  Existing DNS: ${EXISTING_ALIAS_DNS}"
  echo "  Existing zone: ${EXISTING_ALIAS_ZONE_ID}"
  echo "  Expected DNS: ${ALB_DNS}"
  echo "  Expected zone: ${ALB_ZONE_ID}"
  echo "Not changing the existing record."
  exit 1
fi

echo "Creating Route 53 alias: ${RECORD_FQDN} -> ${ALB_DNS}"
CHANGE_ID="$(aws route53 change-resource-record-sets \
  --hosted-zone-id "${HOSTED_ZONE_ID}" \
  --change-batch '{
    "Changes": [
      {
        "Action": "CREATE",
        "ResourceRecordSet": {
          "Name": "'"${RECORD_NAME}"'",
          "Type": "A",
          "AliasTarget": {
            "HostedZoneId": "'"${ALB_ZONE_ID}"'",
            "DNSName": "'"${ALB_DNS}"'",
            "EvaluateTargetHealth": false
          }
        }
      }
    ]
  }' \
  --query 'ChangeInfo.Id' \
  --output text)"

echo "Route 53 change submitted: ${CHANGE_ID}"
echo "Check status with:"
echo "aws route53 get-change --id ${CHANGE_ID}"
