namespace DailyAccountPlanner.WrapperPilot;

public sealed class WrapperPilotOptions
{
    public string AzureTenantId { get; init; } = string.Empty;
    public string BotAppId { get; init; } = string.Empty;
    public string BotAppPassword { get; init; } = string.Empty;
    public string PlannerServiceBaseUrl { get; init; } = string.Empty;
    public string PlannerApiExpectedAudience { get; init; } = string.Empty;
    public string PlannerApiScope { get; init; } = string.Empty;
    public double WrapperForwardTimeoutSeconds { get; init; } = 300.0;
    public double WrapperLongRunningAckThresholdSeconds { get; init; } = 10.0;
    public bool WrapperEnableLongRunningMessages { get; init; } = true;
    public string WrapperDebugExpectedAudience { get; init; } = string.Empty;

    public static WrapperPilotOptions FromConfiguration(IConfiguration configuration)
    {
        var botAppId = GetRequired(configuration, "BOT_APP_ID", "MicrosoftAppId");
        var plannerExpectedAudience = GetOptional(configuration, "PLANNER_API_EXPECTED_AUDIENCE");
        var plannerScope = GetOptional(configuration, "PLANNER_API_SCOPE");
        if (string.IsNullOrWhiteSpace(plannerScope) && !string.IsNullOrWhiteSpace(plannerExpectedAudience))
        {
            plannerScope = $"{plannerExpectedAudience.TrimEnd('/')}/access_as_user";
        }

        return new WrapperPilotOptions
        {
            AzureTenantId = GetOptional(configuration, "AZURE_TENANT_ID", "MicrosoftAppTenantId"),
            BotAppId = botAppId,
            BotAppPassword = GetOptional(configuration, "BOT_APP_PASSWORD", "MicrosoftAppPassword"),
            PlannerServiceBaseUrl = GetOptional(configuration, "PLANNER_SERVICE_BASE_URL").TrimEnd('/'),
            PlannerApiExpectedAudience = plannerExpectedAudience,
            PlannerApiScope = plannerScope,
            WrapperForwardTimeoutSeconds = GetPositiveDouble(configuration, "WRAPPER_FORWARD_TIMEOUT_SECONDS", 300.0),
            WrapperLongRunningAckThresholdSeconds = GetPositiveDouble(configuration, "WRAPPER_LONG_RUNNING_ACK_THRESHOLD_SECONDS", 10.0),
            WrapperEnableLongRunningMessages = GetBoolean(configuration, "WRAPPER_ENABLE_LONG_RUNNING_MESSAGES", true),
            WrapperDebugExpectedAudience = GetOptional(configuration, "WRAPPER_DEBUG_EXPECTED_AUDIENCE"),
        };
    }

    private static string GetRequired(IConfiguration configuration, string primaryKey, string fallbackKey)
    {
        var value = GetOptional(configuration, primaryKey, fallbackKey);
        if (!string.IsNullOrWhiteSpace(value))
        {
            return value;
        }

        throw new InvalidOperationException($"{primaryKey} (or {fallbackKey}) is required.");
    }

    private static string GetOptional(IConfiguration configuration, params string[] keys)
    {
        foreach (var key in keys)
        {
            var value = configuration[key];
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value.Trim();
            }
        }

        return string.Empty;
    }

    private static bool GetBoolean(IConfiguration configuration, string key, bool defaultValue)
    {
        var raw = configuration[key];
        if (string.IsNullOrWhiteSpace(raw))
        {
            return defaultValue;
        }

        var normalized = raw.Trim().ToLowerInvariant();
        return normalized is not ("0" or "false" or "no" or "off");
    }

    private static double GetPositiveDouble(IConfiguration configuration, string key, double defaultValue)
    {
        var raw = configuration[key];
        if (double.TryParse(raw, out var parsed) && parsed > 0)
        {
            return parsed;
        }

        return defaultValue;
    }
}
