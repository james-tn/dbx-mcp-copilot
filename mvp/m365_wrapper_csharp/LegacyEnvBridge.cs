namespace DailyAccountPlanner.WrapperPilot;

internal static class LegacyEnvBridge
{
    public static void Apply(IConfiguration configuration)
    {
        // Keep compatibility with the existing Python wrapper env contract.
        SetIfMissing(configuration, "MicrosoftAppType", "SingleTenant");
        SetIfMissing(configuration, "MicrosoftAppId", FirstNonEmpty(configuration, "MicrosoftAppId", "BOT_APP_ID"));
        SetIfMissing(configuration, "MicrosoftAppPassword", FirstNonEmpty(configuration, "MicrosoftAppPassword", "BOT_APP_PASSWORD"));
        SetIfMissing(configuration, "MicrosoftAppTenantId", FirstNonEmpty(configuration, "MicrosoftAppTenantId", "AZURE_TENANT_ID"));
    }

    private static string FirstNonEmpty(IConfiguration configuration, string primaryKey, string fallbackKey)
    {
        var primary = configuration[primaryKey];
        if (!string.IsNullOrWhiteSpace(primary))
        {
            return primary;
        }

        return configuration[fallbackKey] ?? string.Empty;
    }

    private static void SetIfMissing(IConfiguration configuration, string key, string value)
    {
        if (!string.IsNullOrWhiteSpace(configuration[key]))
        {
            return;
        }

        if (!string.IsNullOrWhiteSpace(value))
        {
            configuration[key] = value;
        }
    }
}
