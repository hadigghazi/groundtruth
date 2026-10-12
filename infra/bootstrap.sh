#!/usr/bin/env bash
# Run once, in Azure Cloud Shell (Bash), signed in to the subscription that holds the credit.
#
# Creates the resource group, and an Entra app that GitHub Actions signs in as with
# OpenID Connect: no password or key is stored anywhere. Everything else (Foundry,
# Container Apps, Event Hubs, registry, monitoring, budget) is created by CI from
# infra/main.bicep. Safe to run again.
set -euo pipefail

REPO="hadigghazi/groundtruth"
LOCATION="${LOCATION:-swedencentral}"
RG="${RG:-groundtruth-rg}"
APP_NAME="groundtruth-github-deploy"

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
echo "Subscription: $(az account show --query name -o tsv) ($SUB)"

echo "Registering resource providers (first time takes a minute)..."
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.Insights Microsoft.CognitiveServices \
          Microsoft.EventHub Microsoft.ContainerRegistry Microsoft.ManagedIdentity Microsoft.Consumption \
          Microsoft.DocumentDB Microsoft.SignalRService Microsoft.Storage Microsoft.ApiManagement; do
  az provider register --namespace "$ns" --wait >/dev/null
done

az group create --name "$RG" --location "$LOCATION" --output none
echo "Resource group $RG in $LOCATION."

APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
fi
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" --output none

# GitHub Actions may sign in only from pushes and manual runs on main. GitHub presents the
# repository either by name or in its "immutable" form, which carries the owner and repository
# ids (repo:owner@123/name@456:...), so both subjects are registered.
OWNER=${REPO%%/*}
NAME=${REPO##*/}
META=$(curl -fsSL "https://api.github.com/repos/$REPO" || true)
OWNER_ID=$(printf '%s' "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["owner"]["id"])' 2>/dev/null || true)
REPO_ID=$(printf '%s' "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null || true)

SUBJECTS=("repo:${REPO}:ref:refs/heads/main")
if [ -n "$OWNER_ID" ] && [ -n "$REPO_ID" ]; then
  SUBJECTS+=("repo:${OWNER}@${OWNER_ID}/${NAME}@${REPO_ID}:ref:refs/heads/main")
else
  echo "Could not read the repository ids from GitHub; registering the plain subject only." >&2
fi

n=0
for SUBJECT in "${SUBJECTS[@]}"; do
  n=$((n + 1))
  if [ -z "$(az ad app federated-credential list --id "$APP_ID" --query "[?subject=='$SUBJECT'].name" -o tsv)" ]; then
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\": \"github-main-${n}\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"${SUBJECT}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" --output none
    echo "Registered sign-in subject: $SUBJECT"
  fi
done

SCOPE="/subscriptions/$SUB/resourceGroups/$RG"
# Contributor creates the resources; the RBAC role lets the template give the app's own
# identity its keyless access (models, Event Hubs, registry). Both stop at this resource group.
for role in "Contributor" "Role Based Access Control Administrator"; do
  az role assignment create --assignee "$APP_ID" --role "$role" --scope "$SCOPE" --output none 2>/dev/null || true
done

echo
echo "Done. On your own machine, run:"
echo
echo "  gh secret set AZURE_CLIENT_ID       -R $REPO -b $APP_ID"
echo "  gh secret set AZURE_TENANT_ID       -R $REPO -b $TENANT"
echo "  gh secret set AZURE_SUBSCRIPTION_ID -R $REPO -b $SUB"
echo "  gh variable set AZURE_DEPLOY        -R $REPO -b true   # turns the deploy workflow on"
echo
echo "Models this subscription can deploy in $LOCATION (for picking the Tactician and writer models):"
az cognitiveservices model list --location "$LOCATION" \
  --query "[?model.format=='OpenAI' && contains(model.name, 'gpt')].{name:model.name, version:model.version}" \
  -o table 2>/dev/null | sort -u | head -40 || true
