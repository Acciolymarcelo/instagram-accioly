# Instagram Accioly

Arquivo dos cinco carrosséis de **@acciolymarceloo** publicados pela API do Instagram em 7 e 8 de novembro de 2026, com as imagens finais, legendas e calendário original.

**Retomando em outra sessão? Leia [CONTINUAR-AQUI.md](CONTINUAR-AQUI.md).**

**A publicação automática foi desativada.** Marcelo agenda e publica manualmente no Instagram ou no Meta Business Suite.

## Conteúdo e calendário

Horário de Brasília (`America/Sao_Paulo`).

| Data | Hora | Conteúdo | Slides |
|---|---|---|---:|
| 07/11/2026 | 12h | [Queda de cabelo durante o emagrecimento](media/01-cabelo/) | 8 |
| 07/11/2026 | 16h | [Musculação e o peso na balança](media/02-musculo/) | 12 |
| 07/11/2026 | 20h | [Testosterona em mulheres](media/04-testosterona/) | 20 |
| 08/11/2026 | 12h | [Refeições gordurosas e Mounjaro](media/05-refeicoes/) | 11 |
| 08/11/2026 | 16h | [Manutenção do peso após Mounjaro](media/03-manutencao/) | 9 |

As legendas completas e a ordem dos slides estão em [manifest.json](manifest.json). As 60 imagens estão em [media](media/). Os endereços públicos utilizados pelo Instagram ficam em [media-urls.json](media-urls.json), fixados a uma versão das imagens.

## Conferência do arquivo

- **Validar publicador e carrosséis** roda testes sem credenciais e confere os hashes dos arquivos em cada alteração relevante.
- [runtime/state.json](runtime/state.json) guarda os horários efetivos, identificadores e permalinks das cinco publicações confirmadas.

## Desenvolvimento

Python 3.11 ou superior, apenas biblioteca padrão, Linux/POSIX.

```sh
python3 -m unittest -v test_publisher
python3 -m unittest discover -s testes -v
python3 publisher.py preflight
python3 publisher.py run-due --max-lateness-seconds 900
```

Os dois últimos comandos usam os arquivos locais para conferir a mídia e simular o calendário. O acesso à Meta pela linha de comando está desativado.
