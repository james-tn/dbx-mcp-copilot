using './main.bicep'

param location = 'eastus'
param prefix = 'ri-poc'

param brokerImage = 'ghcr.io/your-org/ri-auth-broker:latest'
param mcpImage = 'ghcr.io/your-org/ri-revenue-mcp:latest'

param azureOpenAIEndpoint = 'https://your-openai-resource.openai.azure.com/'
param azureOpenAIDeployment = 'gpt-4o-mini'

param createDatabricksWorkspace = true
param databricksWorkspaceName = 'ri-poc-dbx'
param databricksWorkspaceSku = 'premium'
param databricksManagedResourceGroupName = 'ri-poc-dbx-mrg'
param createDatabricksAccessConnector = true
param databricksAccessConnectorName = 'ri-poc-dbx-ucc'

param databricksServerHostname = ''
param databricksHttpPath = ''

param tenantId = '00000000-0000-0000-0000-000000000000'
param brokerClientId = '00000000-0000-0000-0000-000000000000'
param brokerClientSecret = '<set-at-deploy-time>'

param brokerExpectedAudience = 'api://ri-mcp-api'
param brokerAllowedTenants = '00000000-0000-0000-0000-000000000000'
param brokerSharedServiceKey = '<set-at-deploy-time>'

param mcpAllowedSchema = 'ri_poc.revenue'
param mcpMaxRows = 5000
param mcpQueryTimeoutSeconds = 30
