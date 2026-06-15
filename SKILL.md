---
name: excalidraw-diagram
description: Cria e edita diagramas .excalidraw com layout calculado (sem texto cortado, tudo alinhado). Use sempre que o usuário pedir um diagrama, fluxo, timeline, arquitetura, comparação visual ou mencionar Excalidraw — mesmo sem dizer "excalidraw". Gera o arquivo direto; o usuário visualiza pela extensão do VS Code/Obsidian (não precisa de renderer, MCP ou Playwright).
argument-hint: [o que diagramar] [caminho de saída opcional]
---

# Excalidraw Diagram — engine declarativa (excalidraw_dom)

Você descreve uma ÁRVORE (como HTML); a engine mede, posiciona, valida e emite.
NENHUMA coordenada é digitada — referências são objetos Python, nunca ids ou x/y.
As 12 regras de design do usuário são comportamento DEFAULT dos containers.

## Workflow

1. **Planejar o argumento visual.** O diagrama deve ARGUMENTAR, não só exibir:
   timeline para sequência, lanes paralelas para comparação, fan-out para
   1-para-N, zona preenchida para ausência/vazio, anotações ancoradas no
   elemento que provam o ponto. Decida lanes, rows, anotações e cores
   semânticas ANTES de codar.

2. **Ler `references/design-rules.md`** — as regras de layout do usuário,
   paleta e tipografia. Elas têm precedência sobre sua intuição estética.

3. **Escrever um script gerador descartável** (em `/tmp/`) com a API declarativa:

   ```python
   import sys; sys.path.insert(0, "${CLAUDE_SKILL_DIR}/scripts")
   from excalidraw_dom import *

   p = Page("Título do diagrama", autonum=True)   # autonum numera as lanes
   p.add(Chips([("legenda A", "amarelo"), ("legenda B", "azul")]))

   # Lane = label + row + rail automático nas bordas dos cards (regras 2/6/7)
   a = Card("conteúdo\nmultilinha", "cinza")
   b = VStack(Icon("raio", color="#e8a500", fill="#fff3bf"),
              Card("hero com ícone EM CIMA", "amarelo", critical=True), gap=14)
   c = Card("equação", "azul", meta="quando usar (fica sob o card)")
   p.add(Lane("A ideia central", HStack(a, b, c)))   # slots uniformes por default

   p.add(Lane("Gráficos", HStack(
       Graph(fn=lambda t: t*t, color="#2f9e44", ylabel="S", xlabel="t",
             caption="S × t : PARÁBOLA", notes=[(0.6, 0.3, "área = ΔS", "#7a3000")]),
       slots=False), rail=False))

   p.add(Lane("Comparação", Table(["col", "A", "B"], [["x", "1", "2"]]), rail=False))
   p.add(Conclusion("fechamento com fonte maior e paleta verde"))

   # relações por REFERÊNCIA — resolvidas após o layout, com colisão checada
   p.arrow(a, c, label="rótulo ao lado")     # V/H pura ou cotovelo; diagonal = erro
   p.note(c, 1, "anotação numerada", palette="vermelho")
   p.bracket([a, b], "grupo")                # acima, com respiro reservado
   p.attach(Icon("check", color="#2f9e44"), c, side="right")

   p.save("/saida.excalidraw")               # valida TUDO e imprime a árvore
   ```

   Nós: `Card` (shrink-to-fit; `meta=`, `hero=`, `critical=`, `wrap=px`),
   `Conclusion`, `Zone`, `Text(role=...)`, `Icon`, `Chips`, `Table`, `Graph`,
   `Sketch` (escape p/ lápis custom), `Spacer`. Containers: `Lane`, `HStack`
   (slots uniformes + alinhamento por linha de centro são DEFAULT), `VStack`
   (a linha fica no 1º filho sólido — ícone em cima/meta embaixo não tiram o
   card do rail), `Grid`. Paletas: amarelo, laranja, verde, cinza, vermelho,
   azul, conclusao.

   **Colunas entre rows**: para seta vertical entre seções, alinhe com
   `HStack(..., align_x=(nó_desta_row, nó_da_row_anterior))`.

   **Lápis**: `Graph(fn=...)` para curvas (parábola, exponencial...) com eixos
   sketch; `Sketch(draw_fn, w, h)` só quando nenhum nó expressa a forma.

   **Ícones**: glifos prontos em `Doc.ICON_NAMES` (check, x, alert, gear,
   clock, person, db, doc, lupa, raio, coracao). Preenchimento: `fill=` claro
   da paleta + `fill_style="cross-hatch"` (padrão do usuário). Glifo novo?
   Crie no BACKEND (`scripts/excalidraw_engine.py`, novo `elif` em `Doc.icon()`
   + `ICON_NAMES`, coords unitárias 0..1 com `path`/`lerp`/`circle`, sw=1,
   `fillpoly()` antes do traço se fechado) — valide em ~34px e ~80px com o
   usuário antes de dar por pronto.

4. **Rodar e ler a saída.** O `save()` SEMPRE imprime a árvore (uid, frame,
   line_y) e as decisões heurísticas (lado de label, rota de seta, reservas).
   Violações vêm AGREGADAS num `LayoutError` com a correção sugerida — corrija
   tudo num ciclo e regenere. `debug=True` desenha frames fantasma (grupo
   `__debug__`, apagável com 1 clique no Excalidraw). `strict=False` salva
   mesmo com violações, só para inspecionar.

5. **Avisar o usuário para abrir na extensão** (VS Code/Obsidian) e iterar com
   o feedback dele. Para exploração de estilo ou diagramas importantes, ofereça
   2–3 variações de layout para ele escolher a direção.

## Regras de uso da engine

- **Nunca misture** a API declarativa com o backend (`Doc`/`d.card`) no mesmo
  gerador. O backend (`excalidraw_engine.py`) é camada de emissão: só é
  tocado para ADICIONAR glifos de ícone, e qualquer mudança nele exige o
  golden test (regenerar um gerador antigo e comparar byte a byte).
- **Nunca** introduza largura imposta pelo pai (stretch, %, wrap dirigido pelo
  container) — quebra o measure puro em 2 passadas (comentário-guarda no topo
  de `excalidraw_dom.py`).
- Manutenção na engine? Rode `scripts/test_excalidraw_dom.py` (regressão dos
  bugs do ataque adversarial) + o selftest (`python3 excalidraw_dom.py`) antes
  de dar por pronta.

## Editar um diagrama existente

- **Geometria/estrutura** (adicionar elemento, mover seção, mudar textos) →
  regenere pelo script gerador. Guarde o gerador em `/tmp/` durante a sessão.
- **Cosmético pontual** (uma cor, um typo, strokeWidth) → edite o JSON direto.
- **Arquivo editado À MÃO pelo usuário**: nunca sobrescreva — gere com outro
  nome (`-v2`, `-v3`) e deixe ele comparar. As edições manuais dele são
  feedback: compare com o que você gerou, extraia a regra, e proponha
  atualizar `references/design-rules.md`.

## Aprendizado contínuo (loop de gosto)

Quando o usuário corrigir um diagrama manualmente ou der feedback de estilo,
extraia a regra geral (não o caso específico) e adicione em
`references/design-rules.md`. Se a regra for mecanizável, vire-a DEFAULT de
container ou validação em `excalidraw_dom.py` (com teste na regressão) — é
assim que a skill converge para o gosto dele.
