# FS25 Live Dashboard — Boa Vista 4x

Dashboard estático no GitHub Pages, alimentado pelo coletor Python executado no **FraNerdServer**, que consulta a API GIANTS do servidor FS25 hospedado na Fragnet.

- **Site:** https://franerd.github.io/fs25-live-dashboard/
- **Telemetria pública (somente leitura):** https://franerdserver.tail38777c.ts.net/fs25-live/v1/status
- **GitHub:** https://github.com/Franerd/fs25-live-dashboard

## FastNight

Configuração verificada em `fastnight.xml`:

| Faixa no jogo | Escala | Duração real |
|---|---:|---:|
| 07:00–20:00 | 5× | 2h36m |
| 20:00–07:00 | 80× | 8m15s |

Ciclo virtual de 24 horas: **2h44m15s reais**, se não houver pausas nem alterações manuais.

## Atualização automática

1. O serviço Python consulta o endpoint `dedicated-server-stats.xml` a cada **15 minutos (900 segundos)** (o feed da GIANTS pode atualizar em outro intervalo).
2. O servidor Python publica JSON filtrado em `127.0.0.1:18751/v1/status`.
3. O Tailscale Funnel disponibiliza o JSON via HTTPS na rota exclusiva `/fs25-live`, mantendo os serviços existentes de `/ `, `:8443` e `:10000` inalterados.
4. O site consulta esse endpoint a cada 10 segundos e interpola a hora entre amostras conforme a configuração do FastNight.
5. Se o XML demorar a atualizar, a interface continua a **previsão matemática** usando o FastNight, exibindo aviso de incerteza e o último horário efetivamente confirmado. Uma pausa do jogo pode tornar essa previsão incorreta até a próxima amostra.

**Limite de precisão:** `dayTime` não contém a condição de pausa ou a velocidade instantânea. A atualização da API pode ser cacheada. Portanto a página indica estimativas entre leituras e **não afirma precisão absoluta**; sua confiabilidade depende da frequência real de atualização da GIANTS.

## Coletor no FraNerdServer

- Código-fonte: `collector/collector.py`.
- Instalação: `C:\ProgramData\FS25LiveDashboard\collector.py`.
- Configuração privada **não versionada**: `C:\ProgramData\FS25LiveDashboard\private\source.json` (ACL exclusiva Administradores e SYSTEM).
- Tarefa agendada: `FS25LiveDashboard-Collector`, executada como SYSTEM, com Python sem janela.
- Local de escuta: `127.0.0.1:18751`; não abre porta TCP no roteador.

Exemplo de configuração sem segredos em `collector/config.example.json`. O campo `apiCode` deve ser inserido **somente no arquivo privado**, jamais em arquivos públicos. A chave da Fragnet já compartilhada anteriormente deve ser **regenerada**.

**Para ativar a cadência de 15 minutos na instalação existente**, configure `"pollSeconds": 900` no arquivo privado `source.json` com privilégios administrativos e reinicie apenas a tarefa `FS25LiveDashboard-Collector`. Alterar este repositório não atualiza automaticamente a configuração privada instalada no FraNerdServer.

### Testes locais

```powershell
Invoke-RestMethod http://127.0.0.1:18751/v1/status
Get-ScheduledTask -TaskName 'FS25LiveDashboard-Collector'
& 'C:\Program Files\Tailscale\tailscale.exe' funnel status
```

### Desativar somente esta integração (sem afetar outros serviços)

```powershell
& 'C:\Program Files\Tailscale\tailscale.exe' funnel --https=443 --set-path=/fs25-live off
Stop-ScheduledTask -TaskName 'FS25LiveDashboard-Collector'
Disable-ScheduledTask -TaskName 'FS25LiveDashboard-Collector'
```

**Segurança:** o JSON é público e contém apenas estado e contagem de jogadores, mapa, horário de jogo e dados operacionais, sem nomes de jogadores, tokens ou conteúdo do save. O trecho Fragnet → FraNerdServer ainda usa HTTP fornecido pela hospedagem; considere a possibilidade de interceptação de tráfego e gire a chave quando possível.
