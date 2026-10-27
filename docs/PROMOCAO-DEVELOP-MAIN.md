# Promoção automática de develop para main

Após um merge na `develop`, o workflow `GHCR publish and release` executa a validação completa, publica os instaladores Windows/Linux, as imagens GHCR por digest e a pré-release imutável `vX.Y.Z-develop.<SHA12>`.

Somente depois desse workflow terminar **com sucesso**, `develop-to-main-pr.yml` realiza a promoção à `main`:

1. Verifica o SHA de origem, a tag da pré-release, os pacotes obrigatórios e hashes de `BUILD-INFO.txt` e `IMAGES.txt`. Falha em qualquer evidência bloqueia a promoção.
2. Prepara uma branch `release/auto-<sha12>` a partir da `main`, integrando o commit verificado da `develop` e preservando os históricos.
3. Seleciona uma versão SemVer estável livre. Incrementa PATCH automaticamente quando o `VERSION` da `develop` não supera o da `main`, mas respeita versões MINOR/MAJOR explicitamente superiores. Não modifica o `VERSION` da `develop` nem reutiliza tags publicadas.
4. Abre/reutiliza PR regular para `main`, despacha o CI completo no SHA dessa branch de promoção e aguarda aprovação.
5. Confirma novamente as duas branches, o SHA da PR, a ausência de conflito e a exclusividade do SemVer. Só então executa o merge normal, sem `--admin` ou force-push.
6. Dispara explicitamente `ghcr-publish.yml` na `main`. O publicador da `main` compila, verifica checksums/digests e disponibiliza a release estável `vX.Y.Z` no GitHub e GHCR.

O fluxo serializa promoções. Se uma nova alteração chegar à `develop` enquanto uma anterior é validada, a execução anterior não pode promovê-la por engano. Build/release com falha, conflitos, versão ocupada e CI reprovado interrompem a promoção, mantendo os registros de erro.

## Permissões

O GitHub Actions utiliza `contents: write`, `pull-requests: write` e `actions: write`. O repositório deve permitir a criação de PRs por Actions em **Settings → Actions → General → Allow GitHub Actions to create and approve pull requests**. Se não for permitido, configure o segredo opcional `PROMOTION_PR_TOKEN` (GitHub App ou fine-grained PAT com `Contents: write`, `Pull requests: write` e leitura de Actions). A ausência de permissões suficientes deve resultar em erro claro, não em uma falsa publicação.

A automação usa o `GITHUB_TOKEN` da execução para o merge e depois chama explicitamente `workflow_dispatch` na `main`, porque os eventos de push produzidos pelo token nativo podem não disparar os workflows dependentes. Não depende da funcionalidade opcional de auto-merge do GitHub: mescla a PR **depois** do CI verde.

As regras de proteção de branch continuam sendo respeitadas. Se elas exigirem aprovações humanas ou checks específicos não satisfeitos, o merge automatizado será bloqueado, sem contornar essas exigências.

## Segurança de dados e operação

A publicação automatiza artefatos e imagens do repositório, não a implantação em servidores dos clientes. Bancos, migrations e volumes continuam sujeitos aos procedimentos existentes; nenhum `.env`, segredo, backup ou dado de cliente é apagado pela promoção.

Os dois processos completos de build são necessários porque a pré-release valida o commit da `develop`, enquanto a release estável valida o commit final da `main` contendo o novo SemVer. O CI da PR de promoção também valida a integração entre os históricos antes do merge.

Há recuperação manual via `workflow_dispatch`, mas ela exige comprovação de uma execução anterior bem-sucedida do publicador de `develop` para o mesmo SHA.

## Recuperação de falha de permissão

Se o log contiver `GitHub Actions is not permitted to create or approve pull requests`, a aplicação não pode ignorar essa restrição. Habilite uma única vez a opção **Allow GitHub Actions to create and approve pull requests** em `Settings → Actions → General → Workflow permissions`, ou configure o segredo `PROMOTION_PR_TOKEN` de GitHub App/PAT autorizado.

O token padrão pode mostrar `PullRequests: write` no log e, mesmo assim, ser bloqueado pela política de criação de PRs. Por isso a automação faz diagnóstico da política quando ela é legível, antes de preparar a branch de release; quando não for possível lê-la, a falha da criação informa a configuração necessária. A aplicação **não tenta criar PR via outro endpoint** para contornar essa restrição.

**Não reexecute o workflow histórico** `Create develop-to-main promotion PR` do commit `bca69a8a1095`: ele contém o modelo antigo de promoção por push e continuará carregando o YAML do commit original. Depois de ajustar a autorização, use **Actions → Promote verified develop to main → Run workflow** na branch `main`; essa recuperação exige evidências de uma pré-release já concluída para o HEAD da `develop`. Os próximos merges passam a usar somente o promotor de release validada (workflow_run).
