param location string = resourceGroup().location
param namePrefix string = 'dailyacctplanner'
param secureDeployment bool = false
param vnetAddressSpace string = '10.42.0.0/16'
param acaSubnetPrefix string = '10.42.0.0/23'
param databricksPublicSubnetPrefix string = '10.42.2.0/24'
param databricksPrivateSubnetPrefix string = '10.42.3.0/24'
param privateEndpointSubnetPrefix string = '10.42.4.0/24'
param createAcr bool = true
param keyVaultName string = take(replace('${namePrefix}kv', '-', ''), 24)
param acrName string = take(replace('${namePrefix}acr', '-', ''), 50)
param logAnalyticsName string = '${namePrefix}-logs'
param vnetName string = '${namePrefix}-vnet'
param acaSubnetName string = 'aca-infra'
param databricksPublicSubnetName string = 'databricks-public'
param databricksPrivateSubnetName string = 'databricks-private'
param privateEndpointSubnetName string = 'private-endpoints'
param databricksPublicNsgName string = '${namePrefix}-dbx-public-nsg'
param databricksPrivateNsgName string = '${namePrefix}-dbx-private-nsg'

resource databricksPublicNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = if (secureDeployment) {
  name: databricksPublicNsgName
  location: location
  properties: {}
}

resource databricksPrivateNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = if (secureDeployment) {
  name: databricksPrivateNsgName
  location: location
  properties: {}
}

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

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    sku: {
      family: 'A'
      name: 'standard'
    }
    publicNetworkAccess: secureDeployment ? 'Disabled' : 'Enabled'
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (createAcr) {
  name: acrName
  location: location
  sku: {
    name: secureDeployment ? 'Premium' : 'Basic'
  }
  properties: {
    adminUserEnabled: true
    // The operator bootstrap uses az acr build, which runs from Microsoft-managed
    // infrastructure and needs public reachability to log in to the registry.
    publicNetworkAccess: 'Enabled'
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = if (secureDeployment) {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressSpace
      ]
    }
    subnets: [
      {
        name: acaSubnetName
        properties: {
          addressPrefix: acaSubnetPrefix
          delegations: [
            {
              name: 'acaDelegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: databricksPublicSubnetName
        properties: {
          addressPrefix: databricksPublicSubnetPrefix
          networkSecurityGroup: {
            id: databricksPublicNsg.id
          }
          delegations: [
            {
              name: 'databricksPublicDelegation'
              properties: {
                serviceName: 'Microsoft.Databricks/workspaces'
              }
            }
          ]
        }
      }
      {
        name: databricksPrivateSubnetName
        properties: {
          addressPrefix: databricksPrivateSubnetPrefix
          networkSecurityGroup: {
            id: databricksPrivateNsg.id
          }
          delegations: [
            {
              name: 'databricksPrivateDelegation'
              properties: {
                serviceName: 'Microsoft.Databricks/workspaces'
              }
            }
          ]
        }
      }
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource cognitiveDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (secureDeployment) {
  name: 'privatelink.cognitiveservices.azure.com'
  location: 'global'
}

resource openaiDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (secureDeployment) {
  name: 'privatelink.openai.azure.com'
  location: 'global'
}

resource vaultDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (secureDeployment) {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource databricksDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (secureDeployment) {
  name: 'privatelink.azuredatabricks.net'
  location: 'global'
}

resource cognitiveDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (secureDeployment) {
  name: '${cognitiveDns.name}/${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource openaiDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (secureDeployment) {
  name: '${openaiDns.name}/${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource vaultDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (secureDeployment) {
  name: '${vaultDns.name}/${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource databricksDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (secureDeployment) {
  name: '${databricksDns.name}/${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

output secureDeploymentMode bool = secureDeployment
output logAnalyticsWorkspaceName string = logAnalytics.name
output logAnalyticsWorkspaceId string = logAnalytics.id
output keyVaultName string = keyVault.name
output keyVaultId string = keyVault.id
output acrName string = createAcr ? acr.name : ''
output acrId string = createAcr ? acr.id : ''
output vnetId string = secureDeployment ? vnet.id : ''
output acaSubnetId string = secureDeployment ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, acaSubnetName) : ''
output databricksPublicSubnetId string = secureDeployment ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, databricksPublicSubnetName) : ''
output databricksPrivateSubnetId string = secureDeployment ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, databricksPrivateSubnetName) : ''
output privateEndpointSubnetId string = secureDeployment ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, privateEndpointSubnetName) : ''
