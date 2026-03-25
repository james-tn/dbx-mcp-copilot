using Microsoft.Bot.Builder;
using Microsoft.Bot.Schema;

namespace DailyAccountPlanner.WrapperPilot;

public sealed class WrapperPilotBot : ActivityHandler
{
    private const string ReadyMessage = "Daily Account Planner is ready. Send a message to begin.";
    private const string WorkingMessage = "I'm still working on this request. It may take some time.";

    private readonly WrapperPilotOptions _options;
    private readonly BackgroundTurnQueue _backgroundTurnQueue;
    private readonly PlannerServiceClient _plannerClient;
    private readonly ILogger<WrapperPilotBot> _logger;

    public WrapperPilotBot(
        WrapperPilotOptions options,
        BackgroundTurnQueue backgroundTurnQueue,
        PlannerServiceClient plannerClient,
        ILogger<WrapperPilotBot> logger)
    {
        _options = options;
        _backgroundTurnQueue = backgroundTurnQueue;
        _plannerClient = plannerClient;
        _logger = logger;
    }

    protected override async Task OnMessageActivityAsync(
        ITurnContext<IMessageActivity> turnContext,
        CancellationToken cancellationToken)
    {
        var text = (turnContext.Activity.Text ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            await turnContext.SendActivityAsync(ReadyMessage, cancellationToken: cancellationToken);
            return;
        }

        var (delaySeconds, promptText) = ParseDelayCommand(text);
        if (delaySeconds <= 0)
        {
            var plannerReply = await _plannerClient.TryGetQuickReplyAsync(
                sessionId: turnContext.Activity.Conversation?.Id ?? string.Empty,
                userText: promptText,
                cancellationToken: cancellationToken);
            await turnContext.SendActivityAsync(plannerReply, cancellationToken: cancellationToken);
            return;
        }

        if (!_options.WrapperEnableLongRunningMessages ||
            delaySeconds <= _options.WrapperLongRunningAckThresholdSeconds)
        {
            await Task.Delay(TimeSpan.FromSeconds(delaySeconds), cancellationToken);
            await turnContext.SendActivityAsync(
                $"C# pilot inline delayed reply ({delaySeconds:F1}s): {promptText}",
                cancellationToken: cancellationToken);
            return;
        }

        await turnContext.SendActivityAsync(WorkingMessage, cancellationToken: cancellationToken);

        // This demonstrates a built-in queue + continue-conversation shape without
        // custom turn-resume logic in the request path.
        var reference = turnContext.Activity.GetConversationReference();
        await _backgroundTurnQueue.EnqueueAsync(
            new DelayedTurnWorkItem(
                reference,
                promptText,
                TimeSpan.FromSeconds(delaySeconds),
                DateTimeOffset.UtcNow),
            cancellationToken);

        _logger.LogInformation(
            "Queued delayed turn for proactive continuation in C# wrapper pilot. SessionId={SessionId} DelaySeconds={DelaySeconds}",
            turnContext.Activity.Conversation?.Id,
            delaySeconds);
    }

    private static (double DelaySeconds, string PromptText) ParseDelayCommand(string input)
    {
        var trimmed = input.Trim();
        if (!trimmed.StartsWith("/delay ", StringComparison.OrdinalIgnoreCase))
        {
            return (0.0, input);
        }

        var parts = trimmed.Split(' ', 3, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 2 || !double.TryParse(parts[1], out var delaySeconds) || delaySeconds <= 0)
        {
            return (0.0, input);
        }

        var prompt = parts.Length == 3 ? parts[2] : "No prompt text supplied.";
        return (delaySeconds, prompt);
    }
}
