<div align="center">

# Subtitle Auto Translator

**Offline SRT subtitle extraction and translation for MKV media libraries.**

![Status](https://img.shields.io/badge/Status-Active-16A34A?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-2563EB?style=flat-square)
![Casco Digital](https://img.shields.io/badge/Casco-Digital-111827?style=flat-square)
![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![LibreTranslate](https://img.shields.io/badge/LibreTranslate-Offline-00D9FF?style=flat-square)

</div>

---

Traducao automatizada de legendas SRT em arquivos MKV usando LibreTranslate local. Processa bibliotecas de filmes e series, extrai PT-BR embutido quando existe e converte legendas EN -> PT-BR de forma completamente offline.

## Funcionalidades

- **Offline** — LibreTranslate local, sem envio de dados para APIs externas
- **Inteligente** — pula arquivos ja processados, extrai PT-BR embutido e traduz apenas o que falta
- **Batch** — varre recursivamente diretorios de filmes e series
- **Agendavel** — roda via crontab, processa apenas o que e novo

## Quick Start

```bash
git clone https://github.com/cascodigital/subtitle-auto-translator.git
cd subtitle-auto-translator
```

Ajuste os caminhos no `docker-compose.yml`:

```yaml
volumes:
  - /caminho/scripts:/app
  - /caminho/movies:/movies
  - /caminho/tv:/tv
```

```bash
docker compose up -d libretranslate
docker compose run --rm legendas
```

## Agendamento (opcional)

```bash
crontab -e

# Processa legendas diariamente as 03:00
0 3 * * * cd /caminho/subtitle-auto-translator && /usr/bin/docker compose run --rm legendas

# Para o LibreTranslate aos domingos as 04:00
0 4 * * 0 cd /caminho/subtitle-auto-translator && /usr/bin/docker compose down
```

## Como funciona

1. Varre `/movies`, `/tv` e os caminhos opcionais de `EXTRA_MEDIA_DIRS` em busca de `.mkv`
2. Pula se ja existe `.pt-BR.srt`
3. Reutiliza `.en.srt` se ja foi extraido antes
4. Extrai legenda PT/BR embutida quando existe em formato texto
5. Extrai legenda EN do MKV via MKVToolNix quando precisa traduzir
6. Traduz linha por linha via LibreTranslate local
7. Salva `.pt-BR.srt` no mesmo diretorio

Legendas SUP/PGS (formato grafico) sao registradas em `/app/temp/legendassup.txt` para revisao manual.

## Stack

- Python 3 + requests + tqdm
- [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) local em Docker
- [MKVToolNix](https://mkvtoolnix.download/) (extracao de legendas)
- Docker Compose

## Requisitos

- Docker + Docker Compose
- Espaco em disco para os modelos do LibreTranslate
- Arquivos MKV com legendas SRT em ingles

---

Desenvolvido com 🐢 (e cafe) por **Casco Digital**.
