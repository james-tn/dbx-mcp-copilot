using Microsoft.Bot.Builder.Integration.AspNet.Core;
using Microsoft.Bot.Connector.Authentication;

namespace DailyAccountPlanner.WrapperPilot;

public sealed class AdapterWithErrorHandler : CloudAdapter
{
    public AdapterWithErrorHandler(BotFrameworkAuthentication auth, ILogger<AdapterWithErrorHandler> logger)
        : base(auth, logger)
    {
        OnTurnError = async (turnContext, exception) =>
        {
            logger.LogError(exception, "C# wrapper pilot bot turn failed.");
            await turnContext.SendActivityAsync("Daily Account Planner is temporarily unavailable. Please try again in a moment.");
        };
    }
}
