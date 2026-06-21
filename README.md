<div align="center">

# Subtitle Auto Translator

**SRT subtitle extraction and translation for MKV media libraries, with Gemini primary and local LibreTranslate fallback.**

![Status](https://img.shields.io/badge/Status-Active-16A34A?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-2563EB?style=flat-square)
![Casco Digital](https://img.shields.io/badge/Casco-Digital-111827?style=flat-square)
![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Primary-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![LibreTranslate](https://img.shields.io/badge/LibreTranslate-Fallback-00D9FF?style=flat-square)

</div>

---

Traducao automatizada de legendas SRT em arquivos MKV. O fluxo para quando encontra legenda PT/BR embutida, usa Gemini API como tradutor principal para EN -> PT-BR quando precisa traduzir e cai para LibreTranslate local quando o Gemini falha.

## Funcionalidades

- **Gemini primeiro** — usa `gemini-2.5-flash-lite` quando `GEMINI_API_KEY` esta disponivel
- **Fallback local** — LibreTranslate local traduz quando o Gemini falha ou quando nao ha chave configurada
- **Inteligente** — pula arquivos ja processados, respeita PT/BR embutido e traduz apenas o que falta
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
env_file:
  - /caminho/arquivo-com-GEMINI_API_KEY.env
```

Ou use `.env.example` como base para criar um arquivo local com a chave Gemini.

```bash
docker compose up -d libretranslate
docker compose build legendas
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
4. Se existe legenda PT/BR embutida, para o processamento desse arquivo
5. Extrai legenda EN do MKV via MKVToolNix quando precisa traduzir
6. Traduz via Gemini API
7. Usa LibreTranslate local como fallback se o Gemini falhar
8. Salva `.pt-BR.srt` no mesmo diretorio

Legendas SUP/PGS (formato grafico) sao registradas em `/app/temp/legendassup.txt` para revisao manual.

## Stack

- Python 3 + requests + tqdm
- Gemini API (`gemini_api_translate_srt.py`)
- [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) local em Docker
- [MKVToolNix](https://mkvtoolnix.download/) na imagem Docker (extracao de legendas)
- Docker Compose

## Requisitos

- Docker + Docker Compose
- `GEMINI_API_KEY` para o backend principal
- Espaco em disco para os modelos do LibreTranslate
- Arquivos MKV com legendas SRT em ingles

---

Desenvolvido com 🐢 (e cafe) por **Casco Digital**.
