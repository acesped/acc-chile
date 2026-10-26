
name: SISMONAUTA - Noticias sismicas 07 Chile

on:
  # Ejecucion manual desde GitHub Actions
  workflow_dispatch:
    inputs:
      publish:
        description: 'Publicar la noticia en Instagram y Facebook (false = solo vista previa)'
        required: true
        type: boolean
        default: false

  # Ejecucion diaria a las 07:00, hora de Chile
  schedule:
    - cron: '0 7 * * *'
      timezone: 'America/Santiago'

permissions:
  contents: write

concurrency:
  group: sismonauta-noticias-publicaciones
  cancel-in-progress: false

jobs:
  noticia-del-dia:
    name: Buscar noticia y publicar
    runs-on: ubuntu-24.04
    timeout-minutes: 25

    env:
      PYTHONUNBUFFERED: '1'

      # Repositorio y GitHub
      GITHUB_REPOSITORY: ${{ github.repository }}
      GITHUB_REF_NAME: ${{ github.event.repository.default_branch }}
      GH_TOKEN: ${{ github.token }}

      # Instagram
      INSTAGRAM_TOKEN: ${{ secrets.INSTAGRAM_TOKEN }}
      INSTAGRAM_USER_ID: ${{ secrets.INSTAGRAM_USER_ID }}

      # Facebook
      FACEBOOK_PAGE_ID: ${{ secrets.FACEBOOK_PAGE_ID }}
      FACEBOOK_PAGE_TOKEN: ${{ secrets.FACEBOOK_PAGE_TOKEN }}

      IG_GRAPH_VERSION: 'v25.0'

    steps:

      # 1. DESCARGAR REPOSITORIO

      - name: Obtener codigo
        uses: actions/checkout@v4

      # 2. CONFIGURAR PYTHON

      - name: Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      # 3. INSTALAR DEPENDENCIAS

      - name: Instalar dependencias
        run: |
          python -m pip install \
            --disable-pip-version-check \
            'requests>=2.31,<3' \
            'Pillow>=10,<13'

      # 4. VERIFICAR CODIGO

      - name: Comprobar sintaxis
        run: |
          python -m py_compile noticias_sismonauta.py

      # 5. BUSCAR NOTICIA Y GENERAR PUBLICACION

      - name: Buscar noticia y publicar o crear vista previa
        shell: bash
        env:
          PUBLISH: ${{ github.event_name == 'schedule' || inputs.publish == true }}
        run: |
          set -euo pipefail

          args=(--out output_noticias)

          echo "PUBLICAR=$PUBLISH"
          echo "Horario programado: publica automaticamente"
          echo "Ejecucion manual: requiere publish=true"

          if [[ "$PUBLISH" == 'true' ]]; then
            args+=(--publish)
          fi

          python noticias_sismonauta.py "${args[@]}"

      # 6. CONFIRMAR PUBLICACIONES EN META

      - name: Verificar confirmacion de ambas publicaciones
        if: ${{ (github.event_name == 'schedule' || inputs.publish == true) && success() }}
        shell: bash
        run: |
          set -euo pipefail

          python - <<'PYVERIFY'
          import json
          from pathlib import Path

          receipt = Path(
              'output_noticias/estado_publicacion.json'
          )

          if not receipt.exists():
              raise SystemExit(
                  '::error::No existe recibo de publicacion'
              )

          data = json.loads(
              receipt.read_text(encoding='utf-8')
          )

          if data.get('status') != 'published':
              raise SystemExit(
                  '::error::No se publicaron ambas redes: '
                  + str(data.get('status'))
              )

          for site in ('instagram', 'facebook'):
              item = data.get(
                  'targets', {}
              ).get(site, {})

              if (
                  item.get('status') != 'published'
                  or not str(item.get('id', '')).strip()
              ):
                  raise SystemExit(
                      '::error::Publicacion no confirmada: '
                      + site
                  )

              print(
                  'PUBLICACION CONFIRMADA:',
                  site,
                  'ID:',
                  item['id']
              )

          print(
              'SISMONAUTA: ambas publicaciones confirmadas'
          )
          PYVERIFY

      # 7. GUARDAR RESULTADOS Y EVIDENCIAS

      - name: Guardar noticia y arte
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sismonauta-noticias-${{ github.run_id }}-${{ github.run_attempt }}

          path: output_noticias/

          if-no-files-found: warn
          retention-days: 14
