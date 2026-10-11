#!/usr/bin/env bash
# Run once, in Azure Cloud Shell (Bash), signed in to the subscription that holds the credit.
#
# Creates the resource group, and an Entra app that GitHub Actions signs in as with
# OpenID Connect: no password or key is stored anywhere. Everything else (Foundry,
# Container Apps, Event Hubs, registry, monitoring, budget) is created by CI from
# infra/main.bicep. Safe to run again.
set -euo pipefail

REPO="hadigghazi/pitchside"
LOCATION="${LOCATION:-swedencentral}"
RG="${RG:-pitchside-rg}"
APP_NAME="pitchside-github-deploy"

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
echo "Subscription: $(az account show --query name -o tsv) ($SUB)"

echo "Registering resource providers (first time takes a minute)..."
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.Insights Microsoft.CognitiveServices \
          Microsoft.EventHub Microsoft.ContainerRegistry Microsoft.ManagedIdentity Microsoft.Consumption; do
  az provider register --namespace "$ns" --wait >/dev/null
done

az group create --name "$RG" --location "$LOCATION" --output none
echo "Resource group $RG in $LOCATION."

APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
fi
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" --output none

# GitHub Actions may sign in only from pushes and manual runs on main.
if ! az ad app federated-credential list --id "$APP_ID" --query "[?name=='github-main'].name" -o tsv | grep -q github-main; then
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"github-main\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${REPO}:ref:refs/heads/main\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" --output none
fi

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
echo
echo "Models this subscription can deploy in $LOCATION (for picking the Tactician and writer models):"
az cognitiveservices model list --location "$LOCATION" \
  --query "[?model.format=='OpenAI' && contains(model.name, 'gpt')].{name:model.name, version:model.version}" \
  -o table 2>/dev/null | sort -u | head -40 || true
