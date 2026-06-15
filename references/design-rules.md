# Regras de design (gosto do mriago — extraídas de edições manuais reais)

> As "7 regras" originais ganharam adendos numerados conforme novos feedbacks.

Estas regras vieram de comparar diagramas gerados com as correções que o mriago
fez à mão. Elas têm precedência sobre qualquer intuição estética sua.

## As 7 regras de layout

1. **Caixa shrink-to-fit, slot fixo.** A caixa tem o tamanho do próprio conteúdo
   (+padding), nunca largura uniforme forçada. O que é uniforme é o RITMO: slots
   de largura constante, card centralizado no slot, meta centralizada sob o card.

2. **Toda row tem uma linha de centro.** O rail (trilho) é a linha-mestre da
   lane; cards, badges, zonas e textos da row se centralizam verticalmente por
   ela. Elementos de alturas diferentes (ex. um card hero maior) continuam com o
   centro no rail.

3. **Anotações vivem na row do que comentam.** Badge numerado + texto ficam ao
   LADO do elemento (centros verticais alinhados), ou centralizados sob o eixo
   dele — nunca flutuando no espaço entre rows. Badge e texto sempre com centros
   alinhados entre si.

4. **Anotações de grupo (brackets) vão ACIMA da timeline**, no topo do diagrama,
   abraçando exatamente as bordas dos cards do grupo — preservando o respiro
   entre lanes.

5. **Setas verticais/horizontais puras.** Sem diagonais. A seta sai do eixo
   central do elemento de origem e chega no eixo central do destino.

6. **Trilhos nascem e morrem nas bordas dos cards.** Rail começa na borda
   esquerda do primeiro card e termina na borda direita do último — nunca
   comprimento arbitrário, nunca atravessando anotações.

7. **Labels nunca grudados.** Entre a base de um label de seção/lane e o TOPO do
   elemento mais alto da row seguinte: mínimo ~26px de respiro. Cuidado com a
   armadilha: cards se estendem `card_h/2` ACIMA do rail — use `Doc.lane()`,
   que já desconta isso; nunca posicione o rail com avanço fixo após um label.

8. **Texto nunca sobrepõe seta — e sempre AO LADO, não dentro.** Label de seta é
   texto livre PRÓXIMO à linha: 24px de folga, centrado no ponto médio da seta,
   do lado que tiver espaço livre (`label_mode="beside"` = direita,
   `"left"` = esquerda; acima quando horizontal). O modo "bound" (texto dentro
   da seta) existe na engine mas o usuário testou e rejeitou — não usar.

9. **Zonas de destaque têm fundo preenchido** (não só borda tracejada), texto
   escuro centralizado dentro, centradas verticalmente no rail.
   **Conclusões/fechamentos**: fonte maior, texto centralizado, padding generoso.

10. **Curvas e gráficos = lápis (`freedraw`), nunca polyline reta.** Quando o
    argumento pede uma curva (parábola, exponencial, tendência) ou desenho à
    mão, gere com `Doc.freedraw()` + pontos de fórmula + tremida leve
    (~1px de seno) — combina com a estética hand-drawn do resto. Eixos de
    gráfico via `Doc.sketch_axes()`. Curva com cor semântica e `sw=3`;
    eixos cinza escuro. Label do gráfico centrado sob o eixo x.

11. **Ícones a lápis sempre com traço fino (sw=1).** Em glifos pequenos
    (~34px), sw≥2 com simulatePressure vira mancha preenchida — o usuário viu
    e rejeitou. `Doc.icon()` já força sw=1; não engrosse. Cor do ícone segue a
    semântica da paleta (check verde, x vermelho, alert laranja...).
    Preenchimento de glifo: `fill` claro + traço saturado da mesma família
    (igual aos cards); `fill_style="cross-hatch"` é o padrão — o usuário
    comparou os 3 estilos e preferiu cross-hatch (hachure e solid disponíveis).
    O fill é um polígono fechado SOB o traço — glifo novo fechado deve
    chamar `fillpoly()` com os pontos do contorno antes do freedraw.
    Glifo novo = formas mínimas (1–4 traços, silhueta de emoji monocromático),
    desenhado em coords unitárias com path/lerp/circle, e PERSISTIDO na engine
    (novo elif em `Doc.icon()` + `ICON_NAMES`) — nunca um desenho ad-hoc que
    se perde com a sessão.

12. **Tabelas: grade fina, borda e header fortes.** `Doc.table()`: colunas
    medidas pelo conteúdo mais largo, linhas internas sw=1, borda externa e
    header sw=2 com fill claro (`#e7f5ff`/texto `#1864ab`), células centradas.
    Aprovado pelo usuário como está — usar para comparações e matrizes.

## Hierarquia tipográfica

| Papel | fontSize | cor |
|---|---|---|
| Título do diagrama | 28 | `#1e1e1e` |
| Label de seção/lane | 20 | cor semântica (`#1971c2` ativo, `#868e96` secundário) |
| Conteúdo de card | 14–16 | cor de texto do autor/semântica |
| Meta (autor · data, sob o card) | 12 | `#868e96` (ou destaque ex. `#e03131`) |
| Conclusão | 18–20 | escuro sobre fill claro |

## Paleta

Cores codificam SIGNIFICADO (autoria, estado), não decoração. Tripla
(stroke, fill, texto) sempre: stroke saturado, fill claro, texto escuro da mesma família.

| Semântica | stroke | fill | texto |
|---|---|---|---|
| Amarelo (pessoa A / atenção) | `#e8a500` | `#fff3bf` | `#5c4400` |
| Laranja (pessoa B / alerta) | `#e8590c` | `#ffd8a8` | `#7a3000` |
| Verde (pessoa C / sucesso / conclusão) | `#2f9e44` | `#b2f2bb` | `#1b4332` |
| Cinza (neutro / ancestral / inativo) | `#868e96` | `#e9ecef` | `#343a40` |
| Vermelho (problema / vazio / crítica) | `#e03131` | `#ffc9c9` | `#1e1e1e` |
| Azul (estrutura / rails / labels ativos) | `#1971c2` | — | — |
| Conclusão (card final) | `#2f9e44` | `#ebfbee` | `#1b4332` |

Legenda de cores = fileira de chips (`Doc.chips`) no topo, sob o título.

## Estilo geral

- `roughness: 1` em shapes (estética hand-drawn) — mas **`roughness: 0` em
  linhas estruturais** (a engine já força; roughness 1 em linha desenha traço duplo).
- `fontFamily: 3` (monospace) em tudo — é o que torna a medição determinística.
- Elemento crítico/vilão: `strokeWidth: 3` no card. Hero/destaque: card maior
  (fonte 16, padding maior), sempre com centro no rail.
- Badge numerado: círculo 34px preenchido na cor semântica, número branco fs17.
- Fundo branco (o tema escuro fica por conta do viewer do usuário, que inverte).
