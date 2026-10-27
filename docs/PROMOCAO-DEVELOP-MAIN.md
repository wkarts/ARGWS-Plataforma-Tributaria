# Promoção de develop para main

O workflow `.github/workflows/develop-to-main-pr.yml` roda em cada **push na develop**, incluindo merges normais, squash e rebase, e pode ser acionado manualmente. Consulta a comparação `main...develop`. Se há commits não promovidos e nenhuma PR aberta com origem `develop` e destino `main`, cria uma PR **draft**. Se uma PR de promoção já está aberta, mantém a mesma PR e seus comentários; o GitHub atualiza automaticamente seu diff conforme a `develop` avança.

O fluxo apenas prepara revisão. **Não aprova, não faz merge para main, não realiza deploy, não incrementa versões e não publica uma release estável sem aprovação.** A pré-release da develop permanece responsabilidade de `ghcr-publish.yml`. Na main, a tag `vX.Y.Z` tem de ser inédita: versões estáveis já publicadas não podem ser reaproveitadas com outro commit.

## Permissões

O workflow declara `contents: read` e `pull-requests: write`. Para que o `GITHUB_TOKEN` crie PRs, habilite **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests** no repositório. Se essa permissão não estiver disponível, configure o segredo opcional `PROMOTION_PR_TOKEN` (token de GitHub App ou fine-grained PAT com acesso ao repositório e `Pull requests: read/write`). O segredo tem preferência ao token padrão. O workflow informa o erro em vez de alegar criação bem-sucedida.

## Verificações antes de promover

Confirme CI, build da API/Web, instaladores e pacotes de deploy, migrations e `VERSION` novo. A PR continua em rascunho para revisão manual. O workflow não cria uma segunda PR quando uma PR de promoção já existe.
