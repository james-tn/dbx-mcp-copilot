using System.Net.Http.Json;
using System.Text.Json;

namespace DailyAccountPlanner.WrapperPilot;

public sealed class PlannerServiceClient
{
    private readonly HttpClient _httpClient;
    private readonly WrapperPilotOptions _options;
    private readonly ILogger<PlannerServiceClient> _logger;

    public PlannerServiceClient(
        HttpClient httpClient,
        WrapperPilotOptions options,
        ILogger<PlannerServiceClient> logger)
    {
        _httpClient = httpClient;
        _options = options;
        _logger = logger;
    }

    public async Task<string> TryGetQuickReplyAsync(
        string sessionId,
        string userText,
        CancellationToken cancellationToken)
    {
        // For the pilot we keep the fast path available even if planner auth is not wired in yet.
        if (string.IsNullOrWhiteSpace(_options.PlannerServiceBaseUrl))
        {
            return $"C# pilot reply: {userText}";
        }

        var safeSessionId = string.IsNullOrWhiteSpace(sessionId)
            ? $"pilot-{Guid.NewGuid():N}"
            : sessionId;

        try
        {
            var response = await _httpClient.PostAsJsonAsync(
                $"/api/chat/sessions/{safeSessionId}/messages",
                new { text = userText },
                cancellationToken);

            if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
            {
                await _httpClient.PostAsJsonAsync(
                    "/api/chat/sessions",
                    new { session_id = safeSessionId },
                    cancellationToken);

                response = await _httpClient.PostAsJsonAsync(
                    $"/api/chat/sessions/{safeSessionId}/messages",
                    new { text = userText },
                    cancellationToken);
            }

            response.EnsureSuccessStatusCode();

            var payload = await response.Content.ReadFromJsonAsync<Dictionary<string, JsonElement>>(cancellationToken: cancellationToken);
            if (payload is not null &&
                payload.TryGetValue("reply", out var replyElement) &&
                replyElement.ValueKind == JsonValueKind.String)
            {
                var reply = replyElement.GetString();
                if (!string.IsNullOrWhiteSpace(reply))
                {
                    return reply;
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "C# wrapper pilot planner quick-reply call failed; falling back to local echo.");
        }

        return $"C# pilot reply: {userText}";
    }
}
