#!/bin/bash
# usage: bash deploy/apply.sh <image-tag> <aws-region>
set -euo pipefail
export HOME=/root
export KUBECONFIG=/root/.kube/config
TAG="${1:?image tag required}"
REGION="${2:-us-west-2}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Kustomize overlay for this run: sets the image and the IRSA role ARN (both depend on the account).
mkdir -p k8s/overlays/run
cat > k8s/overlays/run/kustomization.yaml <<KUST
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../poc
images:
  - name: streamnotify
    newName: ${REGISTRY}/streamnotify
    newTag: "${TAG}"
patches:
  - target: {kind: ServiceAccount, name: streamnotify}
    patch: |-
      - op: add
        path: /metadata/annotations
        value: {eks.amazonaws.com/role-arn: "arn:aws:iam::${ACCOUNT_ID}:role/streamnotify-role"}
KUST

# The Ingress and Service are checked by the AWS Load Balancer Controller's webhooks,
# so the controller must be running before we apply.
echo "Waiting for the AWS Load Balancer Controller..."
if ! kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=300s; then
  echo "Load Balancer Controller is not ready. Check: kubectl get pods -n kube-system"
  exit 1
fi

applied=0
for attempt in 1 2 3 4 5; do
  if kubectl apply -k k8s/overlays/run; then
    applied=1; break
  fi
  echo "Apply failed (attempt $attempt of 5), retrying in 15s..."
  sleep 15
done
[ "$applied" = "1" ] || { echo "kubectl apply failed after 5 attempts"; exit 1; }

kubectl rollout status deployment/streamnotify -n default --timeout=300s
kubectl get pods -n default -l app=streamnotify
