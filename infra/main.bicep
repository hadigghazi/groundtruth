// Everything Pitchside runs on, in one resource group. Deployed by CI on every push to main.
//
//   Container Apps environment ── pitchside app (API, agents, web; one replica: live sessions
//   │                              are in memory)
//   ├── Log Analytics + Application Insights (traces from Agent Framework, logs)
//   Microsoft Foundry account + project ── two model deployments (Tactician; judge and writers)
//   Event Hubs (Basic) ── match-feed hub: the live event stream between engine and studio
//   Container Registry (Basic) ── images, pulled with the app's identity
//   Budget with alerts to the subscription owner
//
// The app reaches Foundry, Event Hubs and the registry with a user-assigned managed
// identity: no keys or connection strings with secrets anywhere.

targetScope = 'resourceGroup'

@description('Region for everything. Pick one where both model deployments are available.')
param location string = resourceGroup().location

@description('Image for the app. CI first deploys with no image (registry and services only), pushes, then deploys again with the image.')
param appImage string = ''

@description('Model for the Tactician (reasoning over the evidence).')
param largeModel object = { name: 'gpt-4.1-mini', version: '2025-04-14', capacity: 30 }

@description('Model for the Verifier\'s judge and the Persona writers (short, fast calls).')
param smallModel object = { name: 'gpt-4.1-mini', version: '2025-04-14', capacity: 30 }

@description('Role definition id of "Azure AI User", resolved by CI. Empty skips the assignment.')
param aiUserRoleId string = ''

@description('Monthly budget for the resource group, in the billing currency.')
param budgetAmount int = 60

var budgetStart = '2026-10-01' // a budget's start date cannot change once created

var suffix = uniqueString(resourceGroup().id)
var name = 'pitchside'
var tags = { app: name, purpose: 'inside-the-game-hackathon' }

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  eventHubsDataOwner: 'f526a384-b230-433a-b45c-95f59c4a2dec'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-app-id'
  location: location
  tags: tags
}

// ---------------------------------------------------------------- monitoring

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // a cost guard: logs stop for the day past 1 GB
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${name}-insights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ------------------------------------------------------------------ registry

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${name}${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- event hubs

resource eventHubs 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${name}-events-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic', capacity: 1 }
  properties: { disableLocalAuth: true }
}

resource matchFeed 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubs
  name: 'match-feed'
  properties: {
    partitionCount: 2
    messageRetentionInDays: 1
  }
}

resource eventHubsAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(eventHubs.id, identity.id, roles.eventHubsDataOwner)
  scope: eventHubs
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.eventHubsDataOwner)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------------- foundry

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${name}-foundry-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'S0' }
  kind: 'AIServices'
  properties: {
    allowProjectManagement: true // required for Foundry projects; cannot change after create
    customSubDomainName: '${name}-foundry-${suffix}'
    disableLocalAuth: true // identity only, no API keys
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: '${name}-studio'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Pitchside studio'
    description: 'Agents that explain live football moments'
  }
}

resource large 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'tactician'
  sku: { name: 'GlobalStandard', capacity: largeModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: largeModel.name, version: largeModel.version }
  }
}

resource small 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'writer'
  sku: { name: 'GlobalStandard', capacity: smallModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: smallModel.name, version: smallModel.version }
  }
  dependsOn: [large] // one deployment at a time on an account
}

resource foundryOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, roles.openAiUser)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiUserRoleId)) {
  name: guid(foundry.id, identity.id, 'azure-ai-user')
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUserRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ----------------------------------------------------------------- the app

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var projectEndpoint = 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'

resource app 'Microsoft.App/containerApps@2024-03-01' = if (!empty(appImage)) {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto' // HTTP/1.1 upgrade, so WebSockets work
        allowInsecure: false
      }
      registries: [
        { server: registry.properties.loginServer, identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: appImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'PITCHSIDE_LLM', value: 'foundry' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
            { name: 'PITCHSIDE_MODEL_LARGE', value: large.name }
            { name: 'PITCHSIDE_MODEL_SMALL', value: small.name }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential picks this identity
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
            { name: 'EVENTHUB_FQDN', value: '${eventHubs.name}.servicebus.windows.net' }
            { name: 'EVENTHUB_NAME', value: matchFeed.name }
            { name: 'PITCHSIDE_AGENT_BUDGET_S', value: '12' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 30 }
          ]
        }
      ]
      // One replica: a live match lives in memory, so every viewer must reach the same one.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [registryPull, foundryOpenAi, eventHubsAccess]
}

// ----------------------------------------------------------------- budget

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${name}-monthly'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStart }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, contactRoles: ['Owner'] }
      actual80: { enabled: true, operator: 'GreaterThan', threshold: 80, contactRoles: ['Owner'] }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, thresholdType: 'Forecasted', contactRoles: ['Owner'] }
    }
  }
}

output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output projectEndpoint string = projectEndpoint
output appName string = name
