# MyHub Bridge — versão 0.2

Ponte local entre arquivos de solicitação e o MyHubIA, usando Python + Playwright. O Bridge continua independente da Fábrica de Vídeos: recebe um pedido, envia pelo navegador, captura a resposta e organiza os artefatos para uma integração futura.

## O que esta versão faz

- Mantém o perfil Chromium local em `profiles/myhub` (login manual, sem armazenar senha no código).
- Mantém um registro em `data/chat_registry.json` para reutilizar a URL de uma conversa por `chat_key`.
- Recebe solicitações JSON por arquivo, inclusive prompts longos.
- Captura o texto e blocos de código da resposta do assistente.
- Salva blocos `JSON`, `SVG`, `Python`, `HTML`, `CSS`, `JS`, `TXT` e outros formatos reconhecidos em arquivos separados.
- Valida a sintaxe básica de JSON e a estrutura XML de SVG. Não executa Python nem qualquer código retornado pela IA.
- Oferece uma pasta `inbox` para processamento manual ou monitoramento contínuo.

## Instalação inicial (Windows 10/11)

1. Extraia a pasta completa.
2. Execute `instalar.bat`.
3. Execute `login.bat`, entre manualmente no MyHub e pressione ENTER no terminal.
4. Execute `testar.bat` para confirmar que o envio e a captura continuam funcionando.

O primeiro login cria a sessão em `profiles/myhub`. Não compartilhe essa pasta e não a inclua em ZIPs enviados para análise.

## Enviar uma solicitação por JSON

Crie um arquivo `.json` ou `.txt` na pasta `inbox`. Há um modelo estruturado em `examples/request_template.json`. Um `.txt` é enviado integralmente como prompt e usa a conversa `default__general`; use JSON quando precisar separar projetos, finalidades, conversas ou informar o modelo.

Exemplo:

```json
{
  "id": "m1_personagem_001",
  "project": "fabrica_videos",
  "purpose": "motor_m1_recursos_visuais",
  "chat_key": "fabrica_videos__m1_recursos_visuais",
  "model": null,
  "prompt": "Crie os arquivos de um personagem simples para o Motor M1. Retorne cada arquivo em um bloco de código separado, com a linguagem correta (por exemplo, json e svg).",
  "expected_outputs": ["personagem.svg", "personagem.json"]
}
```

Campos:

- `id`: nome usado para organizar o resultado.
- `project` e `purpose`: identificação legível do trabalho.
- `chat_key`: identifica a conversa persistente. Use a mesma chave para continuar o mesmo assunto e outra chave para separar projetos/finalidades.
- `model`: deixe `null` para usar o modelo atualmente selecionado no MyHub. Para escolher um modelo manualmente durante a execução, informe o nome e use `"manual_model_selection": true`. A seleção automática de um modelo específico depende dos seletores reais da interface e ainda precisa ser configurada/testada.
- `manual_model_selection`: quando `true`, pausa com o navegador aberto para você escolher o modelo na interface antes do envio. Se for um chat novo e `model` estiver `null`, o Bridge também pergunta qual nome deve registrar.
- `prompt`: instruções enviadas ao MyHub. Em arquivos TXT, todo o conteúdo do arquivo é o prompt.
- `expected_outputs`: documentação do que se espera receber; não força a IA a gerar os arquivos.

Execute `processar_inbox.bat` para processar os JSON/TXT que estiverem na pasta `inbox`. Os pedidos são movidos para `processed` após a execução ou para `failed` quando ocorre erro. Para monitorar a pasta continuamente, execute `monitorar_inbox.bat`; esse modo envia sem confirmação porque o próprio monitoramento contínuo já é uma autorização operacional. Não deixe arquivos de teste ou rascunhos na `inbox` enquanto o monitor estiver ativo.

Também é possível processar um arquivo diretamente:

```bat
.venv\Scripts\python.exe main.py --job "inbox\meu_pedido.json"
```

Para não pedir confirmação antes de enviar:

```bat
.venv\Scripts\python.exe main.py --job "inbox\meu_pedido.json" --yes
```

## Organização do resultado

Cada solicitação gera uma pasta em `results/<id>/`:

```text
results/
  m1_personagem_001/
    response.txt
    response.json
    artifacts/
      artefato_01.svg
      artefato_02.json
```

`response.json` guarda o retorno integral, metadados da conversa, blocos de código e o resultado das validações. Se a IA não incluir nome de arquivo no bloco, o Bridge cria nomes como `artefato_01.svg` e `artefato_02.json`. Peça ao MyHub para devolver cada arquivo em um bloco de código separado e indicar claramente o tipo de cada bloco.

## Conversas persistentes

O registro `data/chat_registry.json` associa `chat_key` à URL real da conversa. Na primeira solicitação daquela chave, o Bridge abre o endereço-base e grava a URL após a resposta. Nas próximas solicitações, tenta abrir a URL gravada. Isso permite separar, por exemplo:

- `fabrica_videos__roteiros`
- `fabrica_videos__m1_recursos_visuais`
- `fabrica_videos__m2_dioramas`

A persistência depende de o MyHub manter a conversa acessível pela mesma URL. A primeira execução com cada chave precisa terminar com sucesso para registrar o endereço. Não execute duas instâncias simultaneamente usando o mesmo perfil Chromium.

## Seleção de modelo

Quando `model` é `null`, o Bridge conserva o modelo atualmente selecionado naquela conversa. Quando `model` contém um nome, o Bridge só tenta trocar o modelo automaticamente se os seletores forem configurados em `config.json` (`model_selection.trigger_selectors`, `option_selectors` e, opcionalmente, `verify_selectors`). Alternativamente, defina `manual_model_selection: true` no pedido para escolher o modelo com o navegador aberto.

Esses seletores precisam corresponder aos elementos reais do MyHub. Como ainda não fizemos uma inspeção da tela do seletor de modelos, a automação não inventa cliques nem assume que um nome de modelo corresponde a um botão. Se pedir um modelo específico sem configurar os seletores, o envio será interrompido antes de mandar o prompt.

## Limites atuais

- A captura automática de artefatos cobre blocos de código exibidos na resposta. Downloads anexados como cartões/botões do próprio MyHub ainda precisam de inspeção da interface e implementação específica.
- Upload automático de arquivos de entrada ainda não está habilitado.
- A seleção automática do modelo ainda depende dos seletores reais do menu do MyHub.
- O registro de chats guarda URLs; alterações de rota ou mudanças internas do MyHub podem exigir atualizar o registro.
- SVG é validado como XML bem-formado, não por comparação visual nem por renderização no motor M1.
- O Bridge não executa código gerado pela IA.

## Pastas importantes

- `profiles/myhub/`: sessão privada do navegador — nunca compartilhar.
- `inbox/`: solicitações JSON/TXT aguardando envio.
- `processing/`: solicitações em execução.
- `processed/`: solicitações concluídas.
- `failed/`: solicitações que falharam.
- `results/`: respostas e artefatos capturados.
- `data/chat_registry.json`: associação entre chave e URL das conversas.
- `logs/`: registro técnico da execução.

## Segurança

Não inclua `profiles/myhub`, `.venv`, tokens, cookies ou dados pessoais em ZIPs compartilhados ou no Git. Trate todos os arquivos retornados pela IA como conteúdo não confiável. JSON/SVG são dados; scripts são salvos para inspeção, mas nunca executados automaticamente. O Bridge não tenta contornar autenticação, CAPTCHA ou limites de acesso do MyHub.


## Captura de SVG e anexos

O Bridge captura SVGs inline em blocos de código e também tenta baixar anexos exibidos na última mensagem do assistente. Para tarefas do M1, `expected_outputs` deve incluir `svg` (e, quando desejado, `json_sidecar`). Os arquivos baixados são copiados para `results/<job_id>/artifacts/` e validados como XML/SVG ou JSON.

A captura usa seletores comuns de download/anexo e permite seletores específicos em `config.json` em `selectors.attachments`, por exemplo:

```json
"attachments": ["a[download]", "button[aria-label*='Baixar SVG' i]"]
```

O Bridge não executa arquivos nem código retornados pelo MyHub. Se uma resposta não trouxer SVG inline nem anexo reconhecido, o resultado registra diagnósticos da última mensagem; o adaptador M1 não repete automaticamente a mesma solicitação de SVG para evitar ciclos longos.
