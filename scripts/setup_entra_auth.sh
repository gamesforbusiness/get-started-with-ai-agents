#!/bin/bash
# Setup Entra ID (Azure AD) App Registration for Easy Auth
# Run this script after initial deployment to configure authentication.
# Usage: ./scripts/setup_entra_auth.sh

set -e

echo "Setting up Entra ID App Registration for Easy Auth..."

# Check prerequisites
if ! command -v az &> /dev/null; then
    echo "Error: Azure CLI (az) is not installed."
    exit 1
fi

if ! command -v azd &> /dev/null; then
    echo "Error: Azure Developer CLI (azd) is not installed."
    exit 1
fi

# Get the deployed app URL
SERVICE_API_URI=$(azd env get-value SERVICE_API_URI 2>/dev/null || echo "")
if [ -z "$SERVICE_API_URI" ]; then
    echo "Error: SERVICE_API_URI not found. Run 'azd provision' first."
    exit 1
fi

AZURE_ENV_NAME=$(azd env get-value AZURE_ENV_NAME 2>/dev/null || echo "chat-app")

# Check if we already have a client ID
EXISTING_CLIENT_ID=$(azd env get-value ENTRA_AUTH_CLIENT_ID 2>/dev/null || echo "")
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
APP_NAME="chat-app-${AZURE_ENV_NAME}"
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
azd env set ENTRA_AUTH_CLIENT_ID "$APP_ID"
azd env set ENTRA_AUTH_TENANT_ID "$TENANT_ID"

echo ""
echo "Entra ID App Registration configured successfully!"
echo "  Client ID: $APP_ID"
echo "  Tenant ID: $TENANT_ID"
echo ""
echo "Now run 'azd provision' to apply the Easy Auth configuration to your Container App."
echo "Then run 'azd deploy' to deploy the updated application."
