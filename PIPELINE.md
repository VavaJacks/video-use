# Pipeline: Video Use → HyperFrames

Fluxo deste projeto em duas etapas, sempre nesta ordem:

1. **Video Use** corta silêncios, vícios de fala ("é...", "tipo", "né") e repetições / falsos começos → `edit/final.mp4`.
2. **HyperFrames** (`talking-head-recut`) roda logo depois, em cima do `final.mp4`, e adiciona animações e visualizações (títulos, lower-thirds, números, citações, diagramas) sincronizadas com o que está sendo dito.

```
bruto.mp4 ──transcribe──> pack ──LLM decide cortes──> edl.json ──render──> edit/final.mp4
                                                          │
                                   export_transcript.py ──┘──> edit/transcript_output.json
                                                                        │
edit/final.mp4 + transcript_output.json ──HyperFrames talking-head-recut──> output.mp4
```

## Pré-requisitos

- `ffmpeg` / `ffprobe`, Python 3.10+, Node 22+.
- `ELEVENLABS_API_KEY` em `video-use/.env` (Scribe: transcrição palavra-a-palavra **verbatim**, mantém os "é..." e as repetições, que são justamente o que queremos cortar; suporta português).
- `cd video-use && uv sync` (ou `pip install -e .`).

## Etapa 1 — Video Use (cortes)

Com o vídeo em `<pasta>/bruto.mp4`:

```bash
VU=/caminho/para/video-use
python $VU/helpers/transcribe.py <pasta>/bruto.mp4 --language pt
python $VU/helpers/pack_transcripts.py --edit-dir <pasta>/edit
```

O agente lê `edit/takes_packed.md` e monta `edit/edl.json` mantendo só as falas boas:

- **Silêncios:** todo intervalo ≥ 400 ms entre palavras vira ponto de corte (padding de 50 ms antes / 80 ms depois, Hard Rule 7).
- **Repetições / falsos começos:** quando a mesma ideia é dita duas vezes, fica a última tentativa completa (normalmente a mais limpa).
- **Vícios de fala:** "é...", "hum", "tipo assim" isolados entre pausas são removidos.
- Cortes sempre em fronteira de palavra (Hard Rule 6). Sem legendas queimadas nesta etapa (`"subtitles"` fora do EDL) — os gráficos vêm por cima depois.

```bash
python $VU/helpers/render.py <pasta>/edit/edl.json -o <pasta>/edit/preview.mp4 --preview   # revisão
python $VU/helpers/render.py <pasta>/edit/edl.json -o <pasta>/edit/final.mp4               # final
python $VU/helpers/export_transcript.py <pasta>/edit/edl.json                              # ponte
```

`export_transcript.py` converte as palavras mantidas para a linha do tempo do vídeo **já cortado** (`edit/transcript_output.json`, formato `[{text, start, end}]`, o mesmo que o HyperFrames espera).

## Etapa 2 — HyperFrames (animações)

Seguir `hyperframes/skills/talking-head-recut/SKILL.md` com:

- `VIDEO_PATH=<pasta>/edit/final.mp4`
- **Pular o passo 4 (Whisper):** copiar `edit/transcript_output.json` para `$WORK_DIR/transcript.json`. Os tempos já batem com o vídeo cortado e o Scribe é melhor em português que o `small.en` (que é só inglês).
- Passo 5: corrigir nomes próprios / termos técnicos no texto, sem mexer nos tempos.
- Passos 6–9: storyboard dos cards a partir do que é dito, confirmar estilo / proporção, montar a composição.
- Validar com `npx hyperframes lint` e `npx hyperframes check`, depois `npx hyperframes render`.

Resultado final: `$WORK_DIR/output.mp4`.
