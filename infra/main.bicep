// Everything GroundTruth runs on, in one resource group. Deployed by CI on every push to main.
//
//   simulator app --events, 10 Hz ticks--> Event Hubs: match-events (partition = match)
//        ^  (holds the truth log)                  |
//        |                                         v
//   Event Hubs: match-control <-- api app      worker app (scales out; owns match partitions:
//   (start, console, speed, outage,                stats, captions, cues, Agent Framework on Foundry)
//    agents_done)                                  |
//                                                  v
//                         Cosmos DB (serverless) + Web PubSub (one group per match) --> viewers
//
//   Microsoft Foundry account + project: two model deployments, Speech for the commentary
//   Blob Storage: Event Hubs checkpoints | Log Analytics + Application Insights | Registry
//   Optional API Management (consumption) in front of the overlay API | a budget with alerts
//
// Three user-assigned identities, one per role, each with only the roles it needs. No keys
// or connection strings anywhere: every service has local auth turned off.

targetScope = 'resourceGroup'

@description('Region for everything. Pick one where both model deployments are available.')
param location string = resourceGroup().location

@description('Image for the apps. CI first deploys with no image (services only), pushes, then deploys again with it.')
param appImage string = ''

@description('Model for the Tactician (reasoning over the evidence).')
// A new pay-as-you-go subscription gets no GlobalStandard quota for gpt-4.1, but has 500K TPM of
// gpt-5-mini and 200K of gpt-4.1-mini in every region (az cognitiveservices usage list).
param largeModel object = { name: 'gpt-5-mini', version: '2025-08-07', capacity: 100, sku: 'GlobalStandard' }

@description('Model for the judge, the Challenger, the writers and the desk (short, fast calls).')
param smallModel object = { name: 'gpt-4.1-mini', version: '2025-04-14', capacity: 100, sku: 'GlobalStandard' }

@description('Role definition id of "Azure AI User", resolved by CI. Empty skips the assignment.')
param aiUserRoleId string = ''

@description('Web PubSub tier: Free_F1 (20 connections) for development, Standard_S1 for the judging window.')
@allowed(['Free_F1', 'Standard_S1'])
param webPubSubSku string = 'Free_F1'

@description('Put API Management (consumption tier) in front of the overlay API for partners.')
param apiManagement bool = false

@description('Publisher e-mail for API Management, required when apiManagement is true.')
param publisherEmail string = ''

@description('Extra addresses for budget alerts (the subscription Owner always gets them).')
param alertEmails array = []

@description('Monthly budget for the resource group, in the billing currency.')
param budgetAmount int = 120

var budgetStart = '2026-10-01' // a budget's start date cannot change once created

var suffix = uniqueString(resourceGroup().id)
var name = 'groundtruth'
var tags = { app: name, purpose: 'inside-the-game-hackathon' }

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  eventHubsSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  eventHubsReceiver: 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  webPubSubOwner: '12cf5a90-567b-43ae-8102-96cf46c7d9b4'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  speechUser: 'f2dc8367-1007-4938-bd23-fe263f013447'
}
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

// ---------------------------------------------------------------- identities

// index 0 = api, 1 = worker, 2 = simulator
var roleNames = ['api', 'worker', 'simulator']

resource ids 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for r in roleNames: {
  name: '${name}-${r}-id'
  location: location
  tags: tags
}]

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

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (r, i) in roleNames: {
  name: guid(registry.id, r, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: ids[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ------------------------------------------------------------------- storage

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'gt${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource checkpoints 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'checkpoints'
}

resource checkpointWriters 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [1, 2]: {
  name: guid(storage.id, roleNames[i], roles.blobContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobContributor)
    principalId: ids[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ---------------------------------------------------------------- event hubs

resource eventHubs 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${name}-events-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 } // Standard: consumer groups per role
  properties: { disableLocalAuth: true }
}

resource feed 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubs
  name: 'match-events'
  properties: { partitionCount: 4, messageRetentionInDays: 1 }
}

resource control 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubs
  name: 'match-control'
  properties: { partitionCount: 1, messageRetentionInDays: 1 }
}

resource feedWorkers 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: feed
  name: 'workers'
}

resource controlWorkers 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: control
  name: 'workers'
}

resource controlSimulator 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: control
  name: 'simulator'
}

// each role may only send or read what it needs, hub by hub
var feedGrantList = [
  { who: 2, role: roles.eventHubsSender }
  { who: 1, role: roles.eventHubsReceiver }
]
var controlGrantList = [
  { who: 0, role: roles.eventHubsSender }
  { who: 1, role: roles.eventHubsSender }
  { who: 1, role: roles.eventHubsReceiver }
  { who: 2, role: roles.eventHubsReceiver }
]

resource feedGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for g in feedGrantList: {
  name: guid(feed.id, roleNames[g.who], g.role)
  scope: feed
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', g.role)
    principalId: ids[g.who].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

resource controlGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for g in controlGrantList: {
  name: guid(control.id, roleNames[g.who], g.role)
  scope: control
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', g.role)
    principalId: ids[g.who].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// -------------------------------------------------------------------- cosmos

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: '${name}-data-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capabilities: [{ name: 'EnableServerless' }]
    locations: [{ locationName: location, failoverPriority: 0 }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'groundtruth'
  properties: { resource: { id: 'groundtruth' } }
}

resource matchdata 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'matchdata'
  properties: {
    resource: {
      id: 'matchdata'
      partitionKey: { paths: ['/match_id'], kind: 'Hash' }
      defaultTtl: 2592000 // matches clean themselves up after 30 days
    }
  }
}

resource cosmosGrants 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [for (r, i) in roleNames: {
  parent: cosmos
  name: guid(cosmos.id, r, cosmosDataContributor)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    principalId: ids[i].properties.principalId
    scope: cosmos.id
  }
}]

// ---------------------------------------------------------------- web pubsub

resource webPubSub 'Microsoft.SignalRService/webPubSub@2024-03-01' = {
  name: '${name}-live-${suffix}'
  location: location
  tags: tags
  sku: { name: webPubSubSku, capacity: 1 }
  properties: { disableLocalAuth: true }
}

resource liveHub 'Microsoft.SignalRService/webPubSub/hubs@2024-03-01' = {
  parent: webPubSub
  name: 'groundtruth'
  properties: { anonymousConnectPolicy: 'deny' } // viewers connect with a token from /connect
}

resource webPubSubGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (r, i) in roleNames: {
  name: guid(webPubSub.id, r, roles.webPubSubOwner)
  scope: webPubSub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.webPubSubOwner)
    principalId: ids[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

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
    displayName: 'GroundTruth studio'
    description: 'Agents that explain live football moments'
  }
}

resource large 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'tactician'
  sku: { name: largeModel.?sku ?? 'GlobalStandard', capacity: largeModel.capacity } // quota is per model and deployment type
  properties: {
    model: { format: 'OpenAI', name: largeModel.name, version: largeModel.version }
    raiPolicyName: 'Microsoft.DefaultV2' // harm filters on every call, prompt shields on the Ask box
  }
  dependsOn: [project] // the account takes one change at a time: project, then each deployment
}

resource small 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: 'writer'
  sku: { name: smallModel.?sku ?? 'GlobalStandard', capacity: smallModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: smallModel.name, version: smallModel.version }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [large] // one deployment at a time on an account
}

// the worker's agents and the API's desk call the models; only the API speaks
var foundryGrants = concat(
  [{ who: 1, role: roles.openAiUser }, { who: 0, role: roles.openAiUser }, { who: 0, role: roles.speechUser }],
  empty(aiUserRoleId) ? [] : [{ who: 1, role: aiUserRoleId }, { who: 0, role: aiUserRoleId }]
)

resource foundryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for g in foundryGrants: {
  name: guid(foundry.id, roleNames[g.who], g.role)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', g.role)
    principalId: ids[g.who].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// ------------------------------------------------------------------ the apps

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

var platformEnv = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
  { name: 'EVENTHUB_NAMESPACE', value: '${eventHubs.name}.servicebus.windows.net' }
  { name: 'CHECKPOINT_BLOB_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
  { name: 'WEBPUBSUB_ENDPOINT', value: 'https://${webPubSub.properties.hostName}' }
]
var modelEnv = [
  { name: 'GROUNDTRUTH_LLM', value: 'foundry' }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
  { name: 'GROUNDTRUTH_MODEL_LARGE', value: large.name }
  { name: 'GROUNDTRUTH_MODEL_SMALL', value: small.name }
]
var probes = [{ type: 'Liveness', httpGet: { path: '/api/ping', port: 8000 }, periodSeconds: 30 }]

resource api 'Microsoft.App/containerApps@2024-03-01' = if (!empty(appImage)) {
  name: name
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[0].id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: registry.properties.loginServer, identity: ids[0].id }]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: appImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(platformEnv, modelEnv, [
            { name: 'AZURE_CLIENT_ID', value: ids[0].properties.clientId }
            { name: 'GROUNDTRUTH_ROLES', value: 'api' }
            { name: 'GROUNDTRUTH_SPEECH_REGION', value: location }
            { name: 'GROUNDTRUTH_SPEECH_RESOURCE_ID', value: foundry.id }
          ])
          probes: probes
        }
      ]
      // stateless: every read comes from Cosmos DB, every viewer connects to Web PubSub
      scale: { minReplicas: 1, maxReplicas: 3, rules: [{ name: 'http', http: { metadata: { concurrentRequests: '50' } } }] }
    }
  }
  dependsOn: [registryPull, cosmosGrants, controlGrants, webPubSubGrants, foundryAccess]
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = if (!empty(appImage)) {
  name: '${name}-worker'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[1].id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: ids[1].id }]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: appImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: concat(platformEnv, modelEnv, [
            { name: 'AZURE_CLIENT_ID', value: ids[1].properties.clientId }
            { name: 'GROUNDTRUTH_ROLES', value: 'worker' }
            { name: 'GROUNDTRUTH_AGENT_BUDGET_S', value: '20' }
          ])
          probes: probes
        }
      ]
      // replicas share the feed's partitions through the checkpoint store; add one, add matches
      scale: {
        minReplicas: 1
        maxReplicas: 4
        rules: [{ name: 'cpu', custom: { type: 'cpu', metadata: { type: 'Utilization', value: '70' } } }]
      }
    }
  }
  dependsOn: [registryPull, cosmosGrants, feedGrants, controlGrants, webPubSubGrants, foundryAccess, checkpointWriters]
}

resource simulator 'Microsoft.App/containerApps@2024-03-01' = if (!empty(appImage)) {
  name: '${name}-simulator'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[2].id}': {} } }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: ids[2].id }]
    }
    template: {
      containers: [
        {
          name: 'simulator'
          image: appImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(platformEnv, [
            { name: 'AZURE_CLIENT_ID', value: ids[2].properties.clientId }
            { name: 'GROUNDTRUTH_ROLES', value: 'simulator' }
          ])
          probes: probes
        }
      ]
      // one replica: it alone holds each match's truth log until full time
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [registryPull, cosmosGrants, feedGrants, controlGrants, webPubSubGrants, checkpointWriters]
}

// ------------------------------------------------------ partners: API Management

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = if (apiManagement && !empty(appImage)) {
  name: '${name}-apim-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Consumption', capacity: 0 }
  properties: { publisherEmail: publisherEmail, publisherName: 'GroundTruth' }
}

resource overlayApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = if (apiManagement && !empty(appImage)) {
  parent: apim
  name: 'groundtruth'
  properties: {
    displayName: 'GroundTruth overlay API'
    path: 'groundtruth'
    protocols: ['https']
    subscriptionRequired: true
    format: 'openapi+json-link'
    value: 'https://${api.properties.configuration.ingress.fqdn}/openapi.json'
    serviceUrl: 'https://${api.properties.configuration.ingress.fqdn}'
  }
}

resource overlayPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = if (apiManagement && !empty(appImage)) {
  parent: overlayApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base /><rate-limit-by-key calls="120" renewal-period="60" counter-key="@(context.Subscription.Id)" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
}

// ---------------------------------------------------------- the ops workbook

resource opsWorkbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'groundtruth-ops')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'GroundTruth agents'
    category: 'workbook'
    sourceId: insights.id
    serializedData: loadTextContent('workbook.json')
  }
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
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, contactRoles: ['Owner'], contactEmails: alertEmails }
      actual80: { enabled: true, operator: 'GreaterThan', threshold: 80, contactRoles: ['Owner'], contactEmails: alertEmails }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, thresholdType: 'Forecasted', contactRoles: ['Owner'], contactEmails: alertEmails }
    }
  }
}

output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output projectEndpoint string = projectEndpoint
output appName string = name
