# Instagram Accioly

Publicador dos cinco carrosséis aprovados de **@acciolymarceloo**, com as imagens finais, legendas e calendário. Executa pela API oficial do Instagram e pelo GitHub Actions, sem servidor contratado.

**Arquivos enviados ao GitHub; publicação desativada. A conexão direta com a Meta ainda precisa ser configurada e validada.** A conexão do Metricool não transfere sua autorização para este aplicativo. [Guia curto para conectar a conta](CONEXAO-META.txt).

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

## Ativação única

1. Autorizar a conta profissional no próprio aplicativo Meta, usando **Instagram API with Instagram Login**, e gerar o token para publicação. Documentação oficial: [Get started](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started).
2. Em [Actions secrets](https://github.com/Acciolymarcelo/instagram-accioly/settings/secrets/actions), cadastrar `IG_ACCESS_TOKEN` como **repository secret**. Nunca inserir o token no código, no chat ou em variável pública. `IG_USER_ID` é opcional: o executor identifica a conta e exige o usuário `acciolymarceloo` antes de publicar.
3. Em [Actions](https://github.com/Acciolymarcelo/instagram-accioly/actions), executar **Publicador Accioly → Run workflow → preflight**. É uma conferência somente de leitura da conta, limite da API e de cada imagem pública.
4. Com o preflight aprovado e as datas ainda futuras, cadastrar `ACCIOLY_PUBLISHING_ENABLED` com valor `true` em [Actions variables](https://github.com/Acciolymarcelo/instagram-accioly/settings/variables/actions).

O agendador chama o executor diariamente às 12h, 16h e 20h de Brasília. Só publica peças existentes no manifesto, na data e no horário delas. Não existe sexta peça neste lote. GitHub Actions pode iniciar com atraso; uma peça com mais de 15 minutos de atraso fica bloqueada para revisão. Datas vencidas precisam ser reagendadas antes da ativação.

Para pausar, definir `ACCIOLY_PUBLISHING_ENABLED=false`. Isso impede novas execuções de publicação; uma execução já iniciada deve ser cancelada na página Actions.

## Conferência e continuidade

- **Validar publicador e carrosséis** roda testes sem credenciais e confere os hashes dos arquivos em cada alteração relevante.
- **Publicador Accioly** confere conta, imagens, legenda, ordem, data e limite da API antes da publicação.
- O resultado de cada operação fica em `runtime/state.json`, sincronizado no repositório antes de ações irreversíveis. Esse estado contém IDs operacionais, nunca o token, e impede publicação duplicada após falhas.
- Após publicar, o executor consulta o resultado real. Um retorno incerto bloqueia a repetição automática e requer análise.
- Novos lotes exigem atualizar o manifesto e conciliar o estado existente; não apagar o estado para forçar uma nova tentativa.

O token Meta tem validade e pode ser revogado; esta versão não implementa renovação automática. Quando necessário, renovar a conexão e substituir o secret. O projeto público usa runners padrão do GitHub Actions, sem contratação de hospedagem. [Regras de cobrança do GitHub](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Desenvolvimento

Python 3.11 ou superior, apenas biblioteca padrão, Linux/POSIX.

```sh
python3 -m unittest -v test_publisher
python3 -m unittest discover -s testes -v
python3 publisher.py preflight
python3 publisher.py run-due --max-lateness-seconds 900
```

Os dois últimos comandos são locais e não publicam. A verificação real, somente leitura, usa `python3 publisher.py preflight --execute`, com o secret disponível no ambiente. O workflow é a forma prevista de publicação, pois sincroniza o estado durável a cada etapa. Fotos pessoais de referência, fontes de pesquisa e credenciais não integram este repositório.
