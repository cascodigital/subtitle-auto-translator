# Subtitle Auto Translator

![Status](https://img.shields.io/badge/Status-Active-brightgreen)
![License](https://img.shields.io/badge/License-MIT-blue)
![Author](https://img.shields.io/badge/Author-Casco%20Digital-orange)

![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![LibreTranslate](https://img.shields.io/badge/LibreTranslate-Offline-00D9FF?style=flat-square)

Traducao automatizada de legendas SRT em arquivos MKV usando LibreTranslate local. Processa bibliotecas de filmes e series, convertendo legendas EN → PT-BR de forma completamente offline.

## Funcionalidades

- **Offline** — LibreTranslate local, sem envio de dados para APIs externas
- **Inteligente** — pula arquivos ja processados ou com legendas em portugues
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
  - /caminho/temp:/temp
```

```bash
docker compose up -d
```

## Agendamento (opcional)

```bash
crontab -e

# Processa legendas diariamente as 03:00
0 3 * * * cd /caminho/subtitle-auto-translator && /usr/bin/docker compose up -d

# Para containers aos domingos as 04:00
0 4 * * 0 cd /caminho/subtitle-auto-translator && /usr/bin/docker compose down
```

## Como funciona

1. Varre `/movies` e `/tv` em busca de `.mkv`
2. Pula se ja existe `.pt-BR.srt` ou legenda PT embutida
3. Reutiliza `.en.srt` se ja foi extraido antes
4. Extrai legenda EN do MKV via MKVToolNix
5. Traduz linha por linha via LibreTranslate
6. Salva `.pt-BR.srt` no mesmo diretorio

Legendas SUP/PGS (formato grafico) sao registradas em `/temp/legendassup.txt` para revisao manual.

## Stack

- Python 3 + requests + tqdm
- [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) (container Docker, ~1GB de modelos)
- [MKVToolNix](https://mkvtoolnix.download/) (extracao de legendas)
- Docker Compose

## Requisitos

- Docker + Docker Compose
- ~1GB de disco para modelos de traducao
- Arquivos MKV com legendas SRT em ingles

---

Desenvolvido com 🐢 (e cafe) por **Casco Digital**.
