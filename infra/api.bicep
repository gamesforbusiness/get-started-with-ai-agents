param name string
param location string = resourceGroup().location
param tags object = {}

param containerRegistryName string
param identityName string
param containerAppsEnvironmentName string
param azureExistingAIProjectResourceId string
param agentDeploymentName string
param searchConnectionName string
param embeddingDeploymentName string
param aiSearchIndexName string
param embeddingDeploymentDimensions string
param searchServiceEndpoint string
param agentName string
param agentID string
param enableAzureMonitorTracing bool
param otelInstrumentationGenAICaptureMessageContent bool
param projectEndpoint string
param searchConnectionId string
param storageAccountResourceId string
param blobContainerName string
param useAzureAISearch bool = false

@description('Entra ID client ID for Easy Auth')
param entraAuthClientId string = ''
@description('Entra ID tenant ID for Easy Auth')
param entraAuthTenantId string = ''
@description('Storage account name for Table Storage')
param storageAccountName string = ''
@description('Azure Document Intelligence endpoint for file text extraction')
param documentIntelligenceEndpoint string = ''

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

var env = [
  {
    name: 'AZURE_CLIENT_ID'
    value: apiIdentity.properties.clientId
  }
  {
    name: 'AZURE_EXISTING_AIPROJECT_RESOURCE_ID'
    value: azureExistingAIProjectResourceId
  }
  {
    name: 'AZURE_AI_AGENT_NAME'
    value: agentName
  }
  {
    name: 'AZURE_EXISTING_AGENT_ID'
    value: agentID
  }
  {
    name: 'AZURE_AI_AGENT_DEPLOYMENT_NAME'
    value: agentDeploymentName
  }
  {
    name: 'AZURE_AI_EMBED_DEPLOYMENT_NAME'
    value: embeddingDeploymentName
  }
  {
    name: 'AZURE_AI_SEARCH_INDEX_NAME'
    value: aiSearchIndexName
  }
  {
    name: 'AZURE_AI_EMBED_DIMENSIONS'
    value: embeddingDeploymentDimensions
  }
  {
    name: 'RUNNING_IN_PRODUCTION'
    value: 'true'
  }
  {
    name: 'AZURE_AI_SEARCH_CONNECTION_NAME'
    value: searchConnectionName
  }
  {
    name: 'AZURE_AI_SEARCH_ENDPOINT'
    value: searchServiceEndpoint
  }
  {
    name: 'ENABLE_AZURE_MONITOR_TRACING'
    value: enableAzureMonitorTracing
  }
  {
    name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT'
    value: otelInstrumentationGenAICaptureMessageContent
  }
  {
    name: 'AZURE_EXISTING_AIPROJECT_ENDPOINT'
    value: projectEndpoint
  }
  {
    name: 'SEARCH_CONNECTION_ID'
    value: searchConnectionId
  }
  {
    name: 'STORAGE_ACCOUNT_RESOURCE_ID'
    value: storageAccountResourceId
  }
  {
    name: 'AZURE_BLOB_CONTAINER_NAME'
    value: blobContainerName
  }
  {
    name: 'USE_AZURE_AI_SEARCH_SERVICE'
    value: string(useAzureAISearch)
  }
  {
    name: 'ENTRA_AUTH_ENABLED'
    value: string(!empty(entraAuthClientId))
  }
  {
    name: 'ENTRA_AUTH_CLIENT_ID'
    value: entraAuthClientId
  }
  {
    name: 'AZURE_STORAGE_ACCOUNT_NAME'
    value: storageAccountName
  }
  {
    name: 'AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT'
    value: documentIntelligenceEndpoint
  }
]



module app 'core/host/container-app-upsert.bicep' = {
  name: 'container-app-module'
  params: {
    name: name
    location: location
    tags: union(tags, { 'azd-service-name': 'api_and_frontend' })
    identityName: apiIdentity.name
    containerRegistryName: containerRegistryName
    containerAppsEnvironmentName: containerAppsEnvironmentName
    targetPort: 50505
    env: env
    entraAuthClientId: entraAuthClientId
    entraAuthTenantId: entraAuthTenantId
  }
}


output SERVICE_API_IDENTITY_PRINCIPAL_ID string = apiIdentity.properties.principalId
output SERVICE_API_NAME string = app.outputs.name
output SERVICE_API_URI string = app.outputs.uri
