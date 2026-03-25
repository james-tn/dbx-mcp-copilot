using System.Threading.Channels;
using Microsoft.Bot.Schema;

namespace DailyAccountPlanner.WrapperPilot;

public sealed record DelayedTurnWorkItem(
    ConversationReference ConversationReference,
    string PromptText,
    TimeSpan Delay,
    DateTimeOffset EnqueuedAt);

public sealed class BackgroundTurnQueue
{
    private readonly Channel<DelayedTurnWorkItem> _channel =
        Channel.CreateUnbounded<DelayedTurnWorkItem>(new UnboundedChannelOptions
        {
            SingleReader = true,
            SingleWriter = false,
        });

    public ValueTask EnqueueAsync(DelayedTurnWorkItem item, CancellationToken cancellationToken) =>
        _channel.Writer.WriteAsync(item, cancellationToken);

    public IAsyncEnumerable<DelayedTurnWorkItem> ReadAllAsync(CancellationToken cancellationToken) =>
        _channel.Reader.ReadAllAsync(cancellationToken);
}
