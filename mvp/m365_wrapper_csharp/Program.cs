using DailyAccountPlanner.WrapperPilot;
using Microsoft.Bot.Builder;
using Microsoft.Bot.Builder.Integration.AspNet.Core;
using Microsoft.Bot.Connector.Authentication;

var builder = WebApplication.CreateBuilder(args);

builder.Configuration.AddEnvironmentVariables();
LegacyEnvBridge.Apply(builder.Configuration);

var pilotOptions = WrapperPilotOptions.FromConfiguration(builder.Configuration);
builder.Services.AddSingleton(pilotOptions);

builder.Services.AddHttpClient<PlannerServiceClient>(client =>
{
    client.Timeout = TimeSpan.FromSeconds(pilotOptions.WrapperForwardTimeoutSeconds);
    if (!string.IsNullOrWhiteSpace(pilotOptions.PlannerServiceBaseUrl))
    {
        client.BaseAddress = new Uri(pilotOptions.PlannerServiceBaseUrl);
    }
});

builder.Services.AddSingleton<BotFrameworkAuthentication, ConfigurationBotFrameworkAuthentication>();
builder.Services.AddSingleton<AdapterWithErrorHandler>();
builder.Services.AddSingleton<IBotFrameworkHttpAdapter>(sp => sp.GetRequiredService<AdapterWithErrorHandler>());
builder.Services.AddSingleton<CloudAdapter>(sp => sp.GetRequiredService<AdapterWithErrorHandler>());
builder.Services.AddSingleton<IBot, WrapperPilotBot>();
builder.Services.AddSingleton<BackgroundTurnQueue>();
builder.Services.AddHostedService<BackgroundTurnWorker>();

var app = builder.Build();

app.MapGet("/healthz", () => Results.Ok(new
{
    status = "ok",
    service = "m365-wrapper-csharp-pilot",
    longRunningEnabled = pilotOptions.WrapperEnableLongRunningMessages,
}));

app.MapPost("/api/messages", async (HttpRequest request, HttpResponse response, IBotFrameworkHttpAdapter adapter, IBot bot, CancellationToken cancellationToken) =>
{
    await adapter.ProcessAsync(request, response, bot, cancellationToken);
});

app.Run();
