param(
    [string]$ListenAddress = "127.0.0.1",
    [int]$ListenPort,
    [string]$TargetAddress = "127.0.0.1",
    [int]$TargetPort,
    [int]$MaxClients = 64
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ListenPort -or -not $TargetPort) {
    throw "Usage: windows_tcp_relay.ps1 -ListenAddress ADDRESS -ListenPort PORT -TargetAddress ADDRESS -TargetPort PORT"
}
if ($MaxClients -le 0) {
    throw "-MaxClients must be greater than zero"
}

Add-Type -TypeDefinition @"
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;

public static class WindowsTcpRelay
{
    public static void Run(string listenAddress, int listenPort, string targetAddress, int targetPort, int maxClients)
    {
        var listener = new TcpListener(IPAddress.Parse(listenAddress), listenPort);
        listener.Server.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        listener.Start();
        var slots = new SemaphoreSlim(maxClients, maxClients);
        Console.WriteLine(
            "relay listening on {0}:{1} -> {2}:{3} maxClients={4}",
            listenAddress,
            listenPort,
            targetAddress,
            targetPort,
            maxClients);

        while (true)
        {
            var client = listener.AcceptTcpClient();
            if (!slots.Wait(0))
            {
                client.Close();
                continue;
            }
            Task.Run(() => Handle(client, targetAddress, targetPort, slots));
        }
    }

    private static void Handle(TcpClient client, string targetAddress, int targetPort, SemaphoreSlim slots)
    {
        TcpClient target = null;
        try
        {
            target = new TcpClient();
            client.NoDelay = true;
            target.NoDelay = true;
            target.Connect(targetAddress, targetPort);

            var clientStream = client.GetStream();
            var targetStream = target.GetStream();
            var toTarget = Pump(clientStream, targetStream);
            var toClient = Pump(targetStream, clientStream);
            Task.WaitAny(toTarget, toClient);
        }
        catch
        {
        }
        finally
        {
            try { client.Close(); } catch {}
            try { if (target != null) target.Close(); } catch {}
            slots.Release();
        }
    }

    private static async Task Pump(NetworkStream from, NetworkStream to)
    {
        var buffer = new byte[65536];
        try
        {
            while (true)
            {
                int read = await from.ReadAsync(buffer, 0, buffer.Length);
                if (read <= 0)
                {
                    break;
                }
                await to.WriteAsync(buffer, 0, read);
                await to.FlushAsync();
            }
        }
        catch
        {
        }
    }
}
"@

[WindowsTcpRelay]::Run($ListenAddress, $ListenPort, $TargetAddress, $TargetPort, $MaxClients)
