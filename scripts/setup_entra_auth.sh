#!/bin/bash
# Setup Entra ID (Azure AD) App Registration for Easy Auth
# Usage: ./scripts/setup_entra_auth.sh

set -e

# --- Hard-coded target environment ---
E="gamesforbusiness-internal-gpt"
export AZURE_ENV_NAME="$E"
echo "Using azd environment: $E"

echo ""
echo "Setting up Entra ID App Registration for Easy Auth..."

# Check prerequisites
if ! command -v az &> /dev/null; then
    echo "Error: Azure CLI (az) is not installed."
    exit 1
fi

# Custom domain for the application
SERVICE_API_URI="https://internal.gpt.gamesforbusiness.com"

echo "Service URI: $SERVICE_API_URI"

# Check if we already have a client ID (use explicit test to avoid azd errors)
EXISTING_CLIENT_ID=""
if azd env get-value ENTRA_AUTH_CLIENT_ID -e "$E" >/dev/null 2>&1; then
    EXISTING_CLIENT_ID=$(azd env get-value ENTRA_AUTH_CLIENT_ID -e "$E" 2>/dev/null) || true
fi

if [ -n "$EXISTING_CLIENT_ID" ]; then
    echo "Entra Auth Client ID already set: $EXISTING_CLIENT_ID"
    read -p "Do you want to update the redirect URI? (y/N): " UPDATE_REDIRECT
    if [ "$UPDATE_REDIRECT" = "y" ] || [ "$UPDATE_REDIRECT" = "Y" ]; then
        REDIRECT_URI="${SERVICE_API_URI}/.auth/login/aad/callback"
        echo "Updating redirect URI to: $REDIRECT_URI"
        az ad app update --id "$EXISTING_CLIENT_ID" \
            --web-redirect-uris "$REDIRECT_URI"
        echo "Redirect URI updated."
    fi
    exit 0
fi

# Create the App Registration
APP_NAME="chat-app-${E}"
REDIRECT_URI="${SERVICE_API_URI}/.auth/login/aad/callback"

echo "Creating App Registration: $APP_NAME"
echo "Redirect URI: $REDIRECT_URI"

APP_ID=$(az ad app create \
    --display-name "$APP_NAME" \
    --web-redirect-uris "$REDIRECT_URI" \
    --sign-in-audience "AzureADMyOrg" \
    --query appId \
    --output tsv)

if [ -z "$APP_ID" ]; then
    echo "Error: Failed to create App Registration."
    exit 1
fi

echo "App Registration created. Client ID: $APP_ID"

# Get tenant ID
TENANT_ID=$(az account show --query tenantId --output tsv)

# Store values in azd environment
azd env set ENTRA_AUTH_CLIENT_ID "$APP_ID" -e "$E"
azd env set ENTRA_AUTH_TENANT_ID "$TENANT_ID" -e "$E"

echo ""
echo "Entra ID App Registration configured successfully!"
echo "  Client ID: $APP_ID"
echo "  Tenant ID: $TENANT_ID"
echo ""
echo "Next steps:"
echo "  1. AZURE_ENV_NAME=gamesforbusiness-internal-gpt azd provision"
echo "  2. AZURE_ENV_NAME=gamesforbusiness-internal-gpt azd deploy"
