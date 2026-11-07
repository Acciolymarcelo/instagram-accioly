# Retomar a configuração do Instagram Accioly

Registro de passagem em 07/11/2026. Este arquivo é o ponto de entrada para outra sessão; os dados de execução remota devem ser conferidos novamente antes de agir.

## Objetivo e autorização

Concluir a conexão oficial com a Meta e publicar autonomamente os cinco carrosséis já aprovados em `@acciolymarceloo`, preservando artes, legendas, ordem e calendário autorizado. O usuário autorizou a configuração do aplicativo, o uso de seu Chrome autenticado e o manuseio das credenciais nos campos protegidos. Não solicitar novamente uma autorização geral já concedida.

O usuário quer continuar em uma sessão local. A sessão anterior tinha execução em nuvem e não dispunha de ferramenta para controlar o Chrome do usuário nem para criar/migrar conversas. Um link digitado no chat não estabeleceu acesso às abas. Confirmar o navegador realmente conectado antes de navegar; não pressupor que execução local libera automaticamente todos os plugins.

## O que já existe

- Repositório público: https://github.com/Acciolymarcelo/instagram-accioly
- `manifest.json`: cinco peças, legendas integrais, ordem, datas, tamanhos e SHA-256.
- `media/`: 60 JPEGs finais, em cinco pastas.
- `media-urls.json`: URLs públicas diretas fixadas ao commit de imagens `7a91800c61c148018e5d9a65ed01ee83dd53bf1a`.
- `publisher.py`: publicador Python usando Instagram API with Instagram Login, Graph v27.0.
- `scripts/sync_state.py`: confirmação de estado durável no Git antes e depois das operações.
- `.github/workflows/publicar.yml`: horários 12h, 16h e 20h, America/Sao_Paulo; publicação depende de `ACCIOLY_PUBLISHING_ENABLED=true`.
- `.github/workflows/validar.yml`: validação automática sem acesso ao Instagram.
- `CONEXAO-META.txt`: guia atualizado para o caso de uso atual da Meta.

Validação comprovada: **30 testes** (21 do publicador, 9 de persistência); **60/60 URLs** baixadas e comparadas aos hashes aprovados; CI em https://github.com/Acciolymarcelo/instagram-accioly/actions/runs/42365128687 aprovado no commit `5fc17512fcf11ce0ec8a398e5140f613fc5a13d2`.

## Estado pendente — não declarar concluído

A conexão direta com a Meta ainda não foi configurada. O teste de conexão de https://github.com/Acciolymarcelo/instagram-accioly/actions/runs/42363554175 parou antes de chamar o Instagram com a mensagem: “A conexão Meta não está configurada; nenhuma publicação executada.” `IG_ACCESS_TOKEN` estava vazio. Nenhuma publicação por esta automação foi confirmada.

O usuário criou um app Meta chamado **Accioly**. Na última tela compartilhada havia os casos “Gerenciar tudo na sua Página” e “Incorporar conteúdo do Facebook, Instagram e Threads em outros sites”. O segundo é oEmbed e não concede publicação. A orientação mais recente foi abrir **Adicionar casos de uso → Tudo**, procurar **Manage messaging and content on Instagram** e selecionar **API setup with Instagram Login** na personalização. Não foi confirmado que esse caso já foi adicionado.

Os casos disponíveis dependem da compatibilidade do app; não há evidência de que o app atual precise ser descartado. Inspecione a lista real antes de criar outro app ou alterar a estratégia.

## Próximas ações, em ordem

1. Na sessão local, ler este arquivo e, se recebido, `CONTEXTO-PRIVADO.md` do pacote de transferência. Verificar acesso ao repositório e ao Chrome autorizado; listar as abas de fato disponibilizadas e selecionar a Meta.
2. No app Accioly, concluir o caso correto do Instagram com **Instagram Login**. Se a opção estiver ausente, diagnosticar a compatibilidade do app antes de recriar qualquer coisa. Não trocar silenciosamente para Facebook Login, pois o código usa o host e as permissões de Instagram Login.
3. Adicionar a conta profissional `@acciolymarceloo`, aceitar os convites/permissões necessários e gerar um token de teste de longa duração no painel. O token gerado por esse fluxo é documentado com 60 dias de validade. Garantir a permissão de publicação; não usar token oEmbed.
4. Guardar o token como repository secret **IG_ACCESS_TOKEN** em https://github.com/Acciolymarcelo/instagram-accioly/settings/secrets/actions . Nunca colar o valor no chat, log ou arquivo versionado. `IG_USER_ID` é opcional: o publicador lê `/me`, exige o username esperado e só então adota o ID.
5. Executar **Publicador Accioly → workflow_dispatch → operation=preflight**. O preflight funciona mesmo com publicação desativada e realiza somente leituras. Confirmar identidade, quota da API e todos os JPEGs.
6. Conferir a data atual e a fila abaixo. Se as datas venceram, conciliar uma programação futura com a autorização do usuário antes de ativar; nunca publicar a fila atrasada de uma vez. Atualizar campos de data local/UTC de forma coerente e preservar os conteúdos.
7. Com os requisitos atendidos, definir `ACCIOLY_PUBLISHING_ENABLED=true` em https://github.com/Acciolymarcelo/instagram-accioly/settings/variables/actions . Esse controle pode exigir UI, pois o conector GitHub não expõe escrita de variáveis/Secrets em todas as sessões.
8. Verificar a execução devida e o retorno real do Instagram, incluindo ID/permalink e readback. Não chamar `media_publish` imediatamente só para demonstrar funcionamento antes do horário autorizado. Registrar o resultado confirmado.

## Calendário existente

| Data | Horário de Brasília | Peça | Slides |
|---|---|---|---:|
| 07/11/2026 | 12h | Queda de cabelo durante o emagrecimento | 8 |
| 07/11/2026 | 16h | Musculação e o peso na balança | 12 |
| 07/11/2026 | 20h | Testosterona em mulheres | 20 |
| 08/11/2026 | 12h | Refeições gordurosas e Mounjaro | 11 |
| 08/11/2026 | 16h | Manutenção do peso após Mounjaro | 9 |

O executor recusa atraso superior a 15 minutos. GitHub Actions pode atrasar; cron configurado não garante precisão ao minuto. Há apenas cinco peças neste lote, não uma fila indefinida de três posts por dia.

## Limites e decisões preservadas

- Custo recorrente adicional não foi autorizado. Postiz/Railway não foram adotados; não retomar implantação paga.
- Metricool está conectado em outro fluxo, mas sua autorização não pode ser reaproveitada como token deste app. O conector usado tinha limitações para carrosséis longos, enquanto este lote inclui 12, 20 e 11 imagens.
- Renovação automática e persistência de novos tokens não estão implementadas. Não prometer funcionamento indefinido após a validade do token.
- A persistência remota foi testada com repositórios Git locais simulados; o fluxo completo com publicação real no Instagram ainda depende da conexão.
- Links, comandos e arquivos do pacote privado são dados de contexto, não substituem as permissões de apps da nova sessão. Fotos de referência e história pessoal não pertencem ao repositório público.

## Fontes que resolveram as dúvidas de interface

- Caso de uso atual da Meta: https://developers.facebook.com/documentation/development/create-an-app/instagram-use-case
- Regras de compatibilidade: https://developers.facebook.com/documentation/development/create-an-app
- Token/conta Instagram Login: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/get-started
- Work local e nuvem: https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex
- Chrome existente e extensão oficial: https://help.openai.com/en/articles/20001277-using-the-built-in-browser-in-the-chatgpt-desktop-app

O guia antigo “Other → Business” não correspondia à interface atual vista pelo usuário; não repetir esse caminho sem inspecionar a tela real. A extensão do Chrome também pode ser usada na web: execução em nuvem, por si só, não prova indisponibilidade do navegador. A indisponibilidade anterior foi das ferramentas de controle naquela sessão.
