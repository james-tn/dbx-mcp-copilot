targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('POC environment name prefix.')
param prefix string = 'ri-poc'

@description('Container image for Auth Broker service.')
param brokerImage string

@description('Container image for Revenue MCP service.')
param mcpImage string

@description('ACR login server (for example: myacr.azurecr.io).')
param acrLoginServer string

@description('ACR admin username.')
param acrUsername string

@secure()
@description('ACR admin password.')
param acrPassword string

@description('Existing Azure OpenAI endpoint URL.')
param azureOpenAIEndpoint string

@description('Azure OpenAI deployment/model name.')
param azureOpenAIDeployment string

@description('Databricks SQL hostname, for example adb-xxxx.azuredatabricks.net.')
param databricksServerHostname string = ''

@description('Databricks SQL Warehouse HTTP path.')
param databricksHttpPath string = ''

@description('Create a new Premium Databricks workspace (UC-ready baseline).')
param createDatabricksWorkspace bool = true

@description('Name of the Databricks workspace to create when createDatabricksWorkspace=true.')
param databricksWorkspaceName string = '${prefix}-dbx'

@description('SKU for Databricks workspace. Premium is required for Unity Catalog scenarios.')
@allowed([
  'premium'
  'standard'
  'trial'
])
param databricksWorkspaceSku string = 'premium'

@description('Managed resource group name used by the Databricks workspace.')
param databricksManagedResourceGroupName string = '${prefix}-dbx-mrg'

@description('Create Databricks Access Connector for Unity Catalog storage access patterns.')
param createDatabricksAccessConnector bool = true

@description('Name of the Databricks Access Connector resource.')
param databricksAccessConnectorName string = '${prefix}-dbx-ucc'

@description('Entra tenant ID used for token validation.')
param tenantId string

@description('Broker app registration client ID.')
param brokerClientId string

@secure()
@description('Broker app registration client secret.')
param brokerClientSecret string

@description('Audience expected in incoming user token by broker. Example: api://ri-mcp-api')
param brokerExpectedAudience string

@description('Comma-separated list of allowed tenant IDs for broker.')
param brokerAllowedTenants string = ''

@secure()
@description('Shared key used by MCP service when calling broker token endpoint.')
param brokerSharedServiceKey string

@description('Allowed schema for MCP SQL generation.')
param mcpAllowedSchema string = 'ri_poc.revenue'

@description('Default query row limit in MCP service.')
param mcpMaxRows int = 5000

@description('Default query timeout in seconds for MCP service.')
param mcpQueryTimeoutSeconds int = 30

var logAnalyticsName = '${prefix}-law'
var appInsightsName = '${prefix}-appi'
var containerEnvName = '${prefix}-cae'
var brokerAppName = '${prefix}-auth-broker'
var mcpAppName = '${prefix}-revenue-mcp'

resource databricksAccessConnector 'Microsoft.Databricks/accessConnectors@2023-05-01' = if (createDatabricksAccessConnector) {
  name: databricksAccessConnectorName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
}

resource databricksWorkspace 'Microsoft.Databricks/workspaces@2024-05-01' = if (createDatabricksWorkspace) {
  name: databricksWorkspaceName
  location: location
  sku: {
    name: databricksWorkspaceSku
  }
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', databricksManagedResourceGroupName)
  }
}

var createdDatabricksWorkspaceUrl = createDatabricksWorkspace ? string(any(databricksWorkspace).properties.workspaceUrl) : ''
var effectiveDatabricksHost = createDatabricksWorkspace ? (createdDatabricksWorkspaceUrl ?? databricksServerHostname) : databricksServerHostname

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource brokerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: brokerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      activeRevisionsMode: 'Single'
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'broker-client-secret'
          value: brokerClientSecret
        }
        {
          name: 'broker-shared-service-key'
          value: brokerSharedServiceKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'auth-broker'
          image: brokerImage
          env: [
            {
              name: 'AZURE_TENANT_ID'
              value: tenantId
            }
            {
              name: 'BROKER_CLIENT_ID'
              value: brokerClientId
            }
            {
              name: 'BROKER_CLIENT_SECRET'
              secretRef: 'broker-client-secret'
            }
            {
              name: 'BROKER_SCOPE'
              value: '2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default'
            }
            {
              name: 'BROKER_EXPECTED_AUDIENCE'
              value: brokerExpectedAudience
            }
            {
              name: 'BROKER_ALLOWED_TENANTS'
              value: brokerAllowedTenants
            }
            {
              name: 'BROKER_ALLOWED_SERVICE_NAMES'
              value: 'revenue-mcp'
            }
            {
              name: 'BROKER_SHARED_SERVICE_KEY'
              secretRef: 'broker-shared-service-key'
            }
            {
              name: 'DATABRICKS_SERVER_HOSTNAME'
              value: effectiveDatabricksHost
            }
            {
              name: 'DATABRICKS_HTTP_PATH'
              value: databricksHttpPath
            }
            {
              name: 'BROKER_ALLOWED_SCHEMA'
              value: mcpAllowedSchema
            }
            {
              name: 'BROKER_MAX_ROWS'
              value: string(mcpMaxRows)
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: mcpAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      activeRevisionsMode: 'Single'
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'broker-shared-service-key'
          value: brokerSharedServiceKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'revenue-mcp'
          image: mcpImage
          env: [
            {
              name: 'MCP_BROKER_BASE_URL'
              value: 'https://${brokerApp.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'MCP_BROKER_SHARED_KEY'
              secretRef: 'broker-shared-service-key'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAIEndpoint
            }
            {
              name: 'AZURE_TENANT_ID'
              value: tenantId
            }
            {
              name: 'BROKER_CLIENT_ID'
              value: brokerClientId
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: azureOpenAIDeployment
            }
            {
              name: 'DATABRICKS_SERVER_HOSTNAME'
              value: effectiveDatabricksHost
            }
            {
              name: 'DATABRICKS_HTTP_PATH'
              value: databricksHttpPath
            }
            {
              name: 'MCP_ALLOWED_SCHEMA'
              value: mcpAllowedSchema
            }
            {
              name: 'MCP_MAX_ROWS'
              value: string(mcpMaxRows)
            }
            {
              name: 'MCP_QUERY_TIMEOUT_SECONDS'
              value: string(mcpQueryTimeoutSeconds)
            }
            {
              name: 'MCP_TOKEN_REFRESH_SKEW_SECONDS'
              value: '120'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          resources: {
            cpu: json('0.75')
            memory: '1.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

output brokerUrl string = 'https://${brokerApp.properties.configuration.ingress.fqdn}'
output mcpUrl string = 'https://${mcpApp.properties.configuration.ingress.fqdn}'
output containerEnvironmentName string = containerEnv.name
output appInsightsName string = appInsights.name
output databricksWorkspaceUrl string = createDatabricksWorkspace ? 'https://${createdDatabricksWorkspaceUrl}' : ''
output databricksWorkspaceResourceId string = createDatabricksWorkspace ? databricksWorkspace.id : ''
output databricksAccessConnectorResourceId string = createDatabricksAccessConnector ? databricksAccessConnector.id : ''
