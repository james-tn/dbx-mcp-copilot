using Microsoft.Bot.Builder.Integration.AspNet.Core;

namespace DailyAccountPlanner.WrapperPilot;

public sealed class BackgroundTurnWorker : BackgroundService
{
    private readonly BackgroundTurnQueue _queue;
    private readonly CloudAdapter _adapter;
    private readonly WrapperPilotOptions _options;
    private readonly ILogger<BackgroundTurnWorker> _logger;

    public BackgroundTurnWorker(
        BackgroundTurnQueue queue,
        CloudAdapter adapter,
        WrapperPilotOptions options,
        ILogger<BackgroundTurnWorker> logger)
    {
        _queue = queue;
        _adapter = adapter;
        _options = options;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await foreach (var item in _queue.ReadAllAsync(stoppingToken))
        {
            try
            {
                if (item.Delay > TimeSpan.Zero)
                {
                    await Task.Delay(item.Delay, stoppingToken);
                }

                await _adapter.ContinueConversationAsync(
                    _options.BotAppId,
                    item.ConversationReference,
                    async (turnContext, cancellationToken) =>
                    {
                        var elapsedSeconds = (DateTimeOffset.UtcNow - item.EnqueuedAt).TotalSeconds;
                        var message =
                            $"C# pilot delayed reply ({elapsedSeconds:F1}s): {item.PromptText}";
                        await turnContext.SendActivityAsync(message, cancellationToken: cancellationToken);
                    },
                    stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _logger.LogError(
                    ex,
                    "C# wrapper pilot failed to send delayed/proactive response.");
            }
        }
    }
}
