FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

COPY m365_wrapper_csharp/DailyAccountPlanner.WrapperPilot.csproj ./
RUN dotnet restore DailyAccountPlanner.WrapperPilot.csproj

COPY m365_wrapper_csharp/. ./
RUN dotnet publish DailyAccountPlanner.WrapperPilot.csproj -c Release -o /out /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app

ENV ASPNETCORE_URLS=http://+:3978
ENV PORT=3978
EXPOSE 3978

COPY --from=build /out ./
ENTRYPOINT ["dotnet", "DailyAccountPlanner.WrapperPilot.dll"]
