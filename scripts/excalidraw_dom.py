"""excalidraw_dom — "Pauta": camada declarativa de layout sobre a excalidraw_engine.

O autor descreve uma ÁRVORE (Page > Lane/VStack/HStack/Grid > Card/Text/...);
a engine roda um pipeline estrito de fases:

    freeze    -> uids estáveis por caminho (root/lane0/hstack0/card2), parents
    reservas  -> brackets/attaches "above" SOMAM respiro no container alvo
    measure   -> bottom-up, PURO, memoizado (tamanho intrínseco + line())
    arrange   -> top-down, transcreve offsets; frame escrito UMA vez
    overlays  -> setas/brackets/notas resolvidas por REFERÊNCIA a nós
    validate  -> TODAS as violações agregadas num único LayoutError
    emit/save -> JSON via backend Doc (excalidraw_engine intocada)

Protocolo de LINHA DE CENTRO (análogo a baseline): todo nó expõe line() — o
offset do próprio topo até seu eixo de alinhamento. HStack alinha filhos pela
linha; VStack delega a linha ao primeiro filho SÓLIDO (anchor). Assim
"card com meta embaixo" e "ícone sobre o card" mantêm o card no rail POR
CONSTRUÇÃO, e o rail é um OUTPUT do layout, nunca um input.

== COMENTÁRIO-GUARDA (não remova; vale para futuras manutenções) ==
NUNCA introduza largura imposta pelo pai (stretch, %, min/max-width, wrap
dirigido pelo container). measure() ser função pura da subárvore é o que
garante layout em 2 passadas O(n) sem reflow circular/fixpoint. Quebra de
texto é SEMPRE decisão local da folha (wrap= em px, resolvido no construtor).
Nenhuma API aceita id de string do autor: a referência é o objeto Python.
"""
import difflib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from excalidraw_engine import Doc, measure, table_size

AZ = "#1971c2"
PALETAS = {
    "amarelo":   ("#e8a500", "#fff3bf", "#5c4400"),
    "laranja":   ("#e8590c", "#ffd8a8", "#7a3000"),
    "verde":     ("#2f9e44", "#b2f2bb", "#1b4332"),
    "cinza":     ("#868e96", "#e9ecef", "#343a40"),
    "vermelho":  ("#e03131", "#ffc9c9", "#1e1e1e"),
    "azul":      ("#1971c2", "#e7f5ff", "#1864ab"),
    "conclusao": ("#2f9e44", "#ebfbee", "#1b4332"),
}
# tipografia por PAPEL (design-rules: hierarquia tipográfica) — fs nunca é
# decisão solta do autor; role é o caminho default, fs= só override raro.
ROLES = {
    "title":  (28, "#1e1e1e"),
    "label":  (20, AZ),
    "body":   (14, "#1e1e1e"),
    "meta":   (12, "#868e96"),
}

GAP_ARROW = 8      # folga entre seta e borda do nó
MIN_JOG = 16       # cotovelo menor que isso é ilegível -> erro, nunca snap
TOL = 0.75         # tolerância de "mesmo eixo"
INFL = 6           # inflação de AABB no teste de colisão de setas
CLEAR_BRACKET = 18 # respiro entre bracket e topo dos cards


class ConstructionError(ValueError):
    """erro na montagem da árvore — mensagem diz O QUE corrigir."""


class LayoutError(RuntimeError):
    """todas as violações de layout/relacionais, agregadas (corrija em 1 ciclo)."""


def _pal(p):
    if isinstance(p, (tuple, list)):
        return tuple(p)
    if p not in PALETAS:
        hint = difflib.get_close_matches(p, PALETAS, n=1)
        sug = f" — você quis dizer '{hint[0]}'?" if hint else ""
        raise ConstructionError(f"paleta desconhecida: '{p}'{sug} (opções: {', '.join(PALETAS)})")
    return PALETAS[p]


def wrap_text(s, fs, max_w):
    """quebra gulosa por palavras na largura máxima em px (monospace, determinístico)."""
    out = []
    for line in s.split("\n"):
        cur = ""
        for w in line.split(" "):
            cand = (cur + " " + w).strip()
            if not cur or measure(cand, fs)[0] <= max_w:
                cur = cand
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return "\n".join(out)


def _boxes_intersect(a, b, tol=1.0):
    return (min(a[2], b[2]) - max(a[0], b[0]) > tol
            and min(a[3], b[3]) - max(a[1], b[1]) > tol)


def _seg_hits_box(x1, y1, x2, y2, box):
    """segmento V ou H puro vs AABB inflada."""
    bx1, by1, bx2, by2 = box[0] - INFL, box[1] - INFL, box[2] + INFL, box[3] + INFL
    if abs(x1 - x2) < 0.01:  # vertical
        return bx1 <= x1 <= bx2 and min(y1, y2) < by2 and max(y1, y2) > by1
    return by1 <= y1 <= by2 and min(x1, x2) < bx2 and max(x1, x2) > bx1


# ============================== nós ==============================
class Node:
    solid = False      # participa de rails e extents estruturais
    tangible = True    # participa do teste de sobreposição

    def __init__(self, name=None):
        self.children = []
        self.parent = None
        self.name = name
        self.uid = None
        self.frame = None      # (x, y, w, h) — escrito UMA vez no arrange
        self._size = None
        self._line = None
        self._xoff = []
        self._yoff = []
        self._top_reserve = 0  # somado pelo pai ANTES deste nó (brackets/attaches)

    # ---- fase freeze ----
    def freeze(self, parent, path):
        if self.uid is not None:
            raise ConstructionError(
                f"nó usado em DOIS lugares da árvore: {self.uid} e {path} — "
                "crie duas instâncias (ou um componente-função que retorne nós novos)")
        self.parent, self.uid = parent, path
        for i, c in enumerate(self.children):
            c.freeze(self, f"{path}/{c.name or type(c).__name__.lower()}{i}")

    def _reset(self):
        """limpa estado derivado (uid, frame, caches, reservas) — torna save()
        re-chamável e impede que um measure() pré-save congele tamanhos SEM as
        reservas de bracket/attach (bug confirmado no ataque adversarial)."""
        self.uid = None
        self.parent = None
        self.frame = None
        self._size = None
        self._line = None
        self._xoff, self._yoff = [], []
        self._top_reserve = 0
        if hasattr(self, "_extra_gap"):
            self._extra_gap = 0
        for c in self.children:
            c._reset()

    # ---- fase measure (pura) ----
    def measure(self):
        if self._size is None:
            self._size = self._measure()
            if self._line is None:
                self._line = self._size[1] / 2
        return self._size

    def _measure(self):
        return (0, 0)

    def line(self):
        self.measure()
        return self._line

    # ---- fase arrange ----
    def arrange(self, x, y):
        if self.frame is not None:
            raise LayoutError(f"{self.uid}: frame escrito duas vezes (bug da engine)")
        w, h = self.measure()
        self.frame = (x, y, w, h)
        self._arrange()

    def _arrange(self):
        for c, dx, dy in zip(self.children, self._xoff, self._yoff):
            c.arrange(self.frame[0] + dx, self.frame[1] + dy)

    # ---- geometria realizada ----
    @property
    def x(self): return self.frame[0]
    @property
    def y(self): return self.frame[1]
    @property
    def w(self): return self.frame[2]
    @property
    def h(self): return self.frame[3]
    @property
    def cx(self): return self.x + self.w / 2
    @property
    def cy(self): return self.y + self.h / 2
    @property
    def left(self): return self.x
    @property
    def right(self): return self.x + self.w
    @property
    def top(self): return self.y
    @property
    def bottom(self): return self.y + self.h
    @property
    def line_y(self): return self.y + self.line()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def solid_boxes(self):
        """AABBs (x1,y1,x2,y2) das folhas SÓLIDAS da subárvore (p/ rails e setas)."""
        if self.solid and not self.children:
            yield (self.x, self.y, self.right, self.bottom)
        for c in self.children:
            yield from c.solid_boxes()

    def tangible_boxes(self):
        """(box, uid) das folhas tangíveis (p/ validação de sobreposição)."""
        if not self.children:
            if self.tangible and self.w > 0 and self.h > 0:
                yield ((self.x, self.y, self.right, self.bottom), self.uid)
        for c in self.children:
            yield from c.tangible_boxes()

    def emit(self, d):
        for c in self.children:
            c.emit(d)

    def _translate(self, dx):
        if self.frame:
            x, y, w, h = self.frame
            self.frame = (x + dx, y, w, h)
        for c in self.children:
            c._translate(dx)


# ----------------------------- folhas -----------------------------
class Text(Node):
    def __init__(self, s, role="body", fs=None, color=None, wrap=None, align="left", name=None):
        super().__init__(name)
        if role not in ROLES:
            hint = difflib.get_close_matches(role, ROLES, n=1)
            raise ConstructionError(f"role desconhecido: '{role}'"
                                    + (f" — você quis dizer '{hint[0]}'?" if hint else ""))
        rfs, rcolor = ROLES[role]
        self.fs = fs or rfs
        self.color = color or rcolor
        self.s = wrap_text(s, self.fs, wrap) if wrap else s
        self.align = align
        self.tangible = True

    def _measure(self):
        return measure(self.s, self.fs)

    def emit(self, d):
        d.text(self.uid, self.x, self.y, self.s, self.fs, self.color, align=self.align)


class Card(Node):
    """retângulo shrink-to-fit (regra 1: NÃO existe prop width).
    meta= pendura texto fs12 centralizado sob o card; a LINHA do nó continua no
    centro do card — o rail passa pelo card, a meta fica fora dele (regra 1)."""
    solid = True

    def __init__(self, s, palette="cinza", fs=14, padx=18, pady=16, critical=False,
                 hero=False, wrap=None, ss="solid", meta=None, meta_color="#868e96", name=None):
        super().__init__(name)
        if hero:
            fs, padx, pady = 16, 24, 20
        self.s = wrap_text(s, fs, wrap) if wrap else s
        self.fs, self.padx, self.pady = fs, padx, pady
        self.stroke, self.fill, self.txt = _pal(palette)
        self.sw, self.ss = (3 if critical else 2), ss
        self.meta, self.meta_color = meta, meta_color

    def _box(self):
        tw, th = measure(self.s, self.fs)
        return tw + 2 * self.padx, th + 2 * self.pady

    def _measure(self):
        bw, bh = self._box()
        w, h = bw, bh
        if self.meta:
            mw, mh = measure(self.meta, 12)
            w, h = max(w, mw), h + 14 + mh
        self._line = bh / 2
        return (w, h)

    def solid_boxes(self):
        bw, bh = self._box()
        yield (self.cx - bw / 2, self.y, self.cx + bw / 2, self.y + bh)

    def emit(self, d):
        bw, bh = self._box()
        d.card(self.uid, self.cx, self.y + bh / 2, self.s, self.fs, self.stroke,
               self.fill, txt=self.txt, padx=self.padx, pady=self.pady,
               sw=self.sw, ss=self.ss)
        if self.meta:
            mh = measure(self.meta, 12)[1]
            d.ctext(self.uid + "_meta", self.cx, self.y + bh + 14 + mh / 2,
                    self.meta, 12, self.meta_color)


class Conclusion(Card):
    """fechamento (regra 9): fonte maior, padding generoso, paleta de conclusão."""
    def __init__(self, s, palette="conclusao", wrap=None, name=None):
        super().__init__(s, palette, fs=18, padx=28, pady=22, wrap=wrap, name=name)


class Zone(Node):
    """zona de destaque com fundo preenchido (regra 9)."""
    solid = True

    def __init__(self, s, palette="vermelho", fs=14, padx=30, pady=18, name=None):
        super().__init__(name)
        self.s, self.fs, self.padx, self.pady = s, fs, padx, pady
        self.stroke, self.fill, self.txt = _pal(palette)

    def _measure(self):
        tw, th = measure(self.s, self.fs)
        return (tw + 2 * self.padx, th + 2 * self.pady)

    def emit(self, d):
        d.zone(self.uid, self.x, self.right, self.cy, self.h,
               self.stroke, self.fill, self.s, txt=self.txt, fs=self.fs)


class Icon(Node):
    """glifo a lápis (caixa s×s); caption opcional fs12 embaixo.
    A LINHA fica no centro do glifo (caption pendura fora, como meta de card)."""
    def __init__(self, glyph, s=34, color="#343a40", fill=None,
                 fill_style="cross-hatch", caption=None, name=None):
        super().__init__(name)
        if glyph not in Doc.ICON_NAMES:
            hint = difflib.get_close_matches(glyph, Doc.ICON_NAMES, n=1)
            raise ConstructionError(f"ícone desconhecido: '{glyph}'"
                                    + (f" — você quis dizer '{hint[0]}'?" if hint else "")
                                    + f" (opções: {', '.join(Doc.ICON_NAMES)})")
        self.glyph, self.size, self.color = glyph, s, color
        self.fill, self.fill_style, self.caption = fill, fill_style, caption

    def _measure(self):
        w, h = self.size, self.size
        if self.caption:
            cw, ch = measure(self.caption, 12)
            w, h = max(w, cw), h + 10 + ch
        self._line = self.size / 2
        return (w, h)

    def emit(self, d):
        d.icon(self.uid, self.glyph, self.cx, self.y + self.size / 2,
               s=self.size, color=self.color, fill=self.fill, fill_style=self.fill_style)
        if self.caption:
            ch = measure(self.caption, 12)[1]
            d.ctext(self.uid + "_cap", self.cx, self.y + self.size + 10 + ch / 2,
                    self.caption, 12, "#868e96")


class Chips(Node):
    """legenda: fileira de chips [(label, paleta)]."""
    def __init__(self, items, fs=13, gap=16, name=None):
        super().__init__(name)
        if not items:
            raise ConstructionError("Chips: a lista de chips não pode ser vazia")
        self.items = [(s, *_pal(p)) for s, p in items]
        self.fs, self.gap = fs, gap

    def _measure(self):
        ws = [measure(s, self.fs)[0] + 28 for s, *_ in self.items]
        return (sum(ws) + self.gap * (len(ws) - 1), measure("X", self.fs)[1] + 16)

    def emit(self, d):
        x = self.x
        for i, (s, st, fi, tx) in enumerate(self.items):
            w = measure(s, self.fs)[0] + 28
            d.card(f"{self.uid}_{i}", x + w / 2, self.cy, s, self.fs, st, fi,
                   txt=tx, padx=14, pady=8)
            x += w + self.gap


class Table(Node):
    solid = True

    def __init__(self, header, rows, fs=14, padx=14, pady=10, name=None):
        super().__init__(name)
        self.header, self.rows = header, rows
        self.fs, self.padx, self.pady = fs, padx, pady

    def _measure(self):
        _, _, W, H = table_size(self.header, self.rows, self.fs, self.padx, self.pady)
        return (W, H)

    def emit(self, d):
        d.table(self.uid, self.x, self.y, self.header, self.rows,
                fs=self.fs, padx=self.padx, pady=self.pady)


class Graph(Node):
    """eixos a lápis + curva em espaço unitário (regra 10) — NUNCA coordenada crua.
    fn: t∈[0,1] -> y∈[0,1], ou pts=[(x_u, y_u)] unitários."""
    def __init__(self, fn=None, pts=None, w=260, h=170, color=AZ, caption=None,
                 ylabel=None, xlabel=None, sw=3, n=25, notes=(), name=None):
        super().__init__(name)
        if not fn and not pts:
            raise ConstructionError("Graph precisa de fn= (t->y unitário) ou pts= (lista unitária)")
        if fn and n < 2:
            raise ConstructionError(f"Graph: n={n} — a curva precisa de pelo menos 2 pontos")
        self.fn, self.pts_u = fn, pts
        self.gw, self.gh, self.color, self.sw, self.n = w, h, color, sw, n
        self.caption, self.ylabel, self.xlabel = caption, ylabel, xlabel
        self.notes = notes  # [(x_u, y_u, texto, cor)] em espaço unitário do gráfico
        self.mx = 26

    def _measure(self):
        # margens derivadas do CONTEÚDO: ylabel/xlabel/notes entram no frame
        # (labels fora do frame medido eram invisíveis à validação — bug confirmado)
        ml = max(self.mx, (16 + measure(self.ylabel, 14)[0] / 2 + 4) if self.ylabel else 0)
        mr = max(self.mx, (16 + measure(self.xlabel, 14)[0] / 2 + 4) if self.xlabel else 0)
        mt = (max(0.0, measure(self.ylabel, 14)[1] / 2 - 6) + 2) if self.ylabel else 0.0
        for (xu, yu, s, _c) in self.notes:
            nw, nh = measure(s, 12)
            mt = max(mt, self.gh * yu + nh / 2 - self.gh)
            ml = max(ml, nw / 2 - self.gw * xu)
            mr = max(mr, nw / 2 - self.gw * (1 - xu))
        self._ml, self._mr, self._mt = ml, mr, mt
        cap_h = (12 + measure(self.caption, 14)[1]) if self.caption else 0
        return (self.gw + ml + mr, mt + self.gh + cap_h)

    def emit(self, d):
        ox, oy = self.x + self._ml, self.y + self._mt + self.gh
        d.sketch_axes(self.uid + "_ax", ox, oy, self.gw, self.gh)
        wob = lambda i: math.sin(i * 1.7) * 0.9
        if self.fn:
            pts = [(self.gw * (0.06 + 0.86 * i / (self.n - 1)),
                    -self.gh * (0.06 + 0.80 * self.fn(i / (self.n - 1))) + wob(i))
                   for i in range(self.n)]
        else:
            pts = [(self.gw * px, -self.gh * py + wob(i))
                   for i, (px, py) in enumerate(self.pts_u)]
        d.freedraw(self.uid + "_c", ox, oy, pts, self.color, sw=self.sw)
        if self.ylabel:
            d.ctext(self.uid + "_y", ox - 16, self.y + self._mt + 6, self.ylabel, 14, "#343a40")
        if self.xlabel:
            d.ctext(self.uid + "_x", ox + self.gw + 16, oy, self.xlabel, 14, "#343a40")
        if self.caption:
            ch = measure(self.caption, 14)[1]
            d.ctext(self.uid + "_cap", ox + self.gw / 2, oy + 12 + ch / 2,
                    self.caption, 14, "#343a40")
        for k, (xu, yu, s, color) in enumerate(self.notes):
            d.ctext(f"{self.uid}_n{k}", ox + self.gw * xu, oy - self.gh * yu, s, 12, color)


class Sketch(Node):
    """escape hatch p/ desenho a lápis custom: draw_fn(d, x, y) com origem do nó.
    Prefira Graph/Icon; use isto só quando nenhum nó expressa a forma."""
    def __init__(self, draw_fn, w, h, name=None):
        super().__init__(name)
        self.draw_fn, self._w, self._h = draw_fn, w, h

    def _measure(self):
        return (self._w, self._h)

    def emit(self, d):
        self.draw_fn(d, self.x, self.y)


class Spacer(Node):
    tangible = False

    def __init__(self, h=24, w=0):
        super().__init__()
        self._wh = (w, h)

    def _measure(self):
        return self._wh


# --------------------------- containers ---------------------------
class VStack(Node):
    """pilha vertical. anchor: filho que define a LINHA do stack —
    default: primeiro filho com conteúdo sólido (mantém card no rail quando
    há ícone em cima ou meta embaixo)."""
    def __init__(self, *children, gap=24, align="center", anchor=None, name=None):
        super().__init__(name)
        if align not in ("left", "center", "right"):
            raise ConstructionError(f"VStack: align inválido '{align}' (use left|center|right)")
        self.children = list(children)
        self.gap, self.align, self.anchor = gap, align, anchor

    def _anchor_idx(self):
        if self.anchor == "last":
            return len(self.children) - 1
        if isinstance(self.anchor, int):
            return self.anchor
        if isinstance(self.anchor, Node):
            return self.children.index(self.anchor)
        for i, c in enumerate(self.children):
            if any(True for _ in c.solid_boxes_static()):
                return i
        return 0

    def _measure(self):
        szs = [c.measure() for c in self.children]
        if not szs:
            return (0, 0)
        W = max(w for w, _ in szs)
        self._xoff, self._yoff = [], []
        y = 0
        for c, (cw, ch) in zip(self.children, szs):
            y += c._top_reserve
            self._xoff.append({"left": 0, "center": (W - cw) / 2, "right": W - cw}[self.align])
            self._yoff.append(y)
            y += ch + self.gap
        H = y - self.gap
        ai = self._anchor_idx()
        self._line = self._yoff[ai] + self.children[ai].line()
        return (W, H)


class HStack(Node):
    """row horizontal. DEFAULTS são as regras: slots=True (ritmo uniforme,
    regra 1) e alinhamento pela LINHA de centro (regra 2)."""
    def __init__(self, *children, gap=44, align="line", slots=True, align_x=None, name=None):
        super().__init__(name)
        if align not in ("line", "top", "center", "bottom"):
            raise ConstructionError(f"HStack: align inválido '{align}' (use line|top|center|bottom)")
        self.children = list(children)
        self.gap, self.align, self.slots = gap, align, slots
        self.align_x = align_x  # (nó_desta_row, nó_de_referência_já_posicionado)

    def _measure(self):
        szs = [c.measure() for c in self.children]
        if not szs:
            return (0, 0)
        if self.align == "line":
            L = max(c.line() for c in self.children)
            D = max(h - c.line() for c, (_, h) in zip(self.children, szs))
            H = L + D
            self._yoff = [L - c.line() for c in self.children]
            self._line = L
        else:
            H = max(h for _, h in szs)
            self._yoff = [{"top": 0, "center": (H - h) / 2, "bottom": H - h}[self.align]
                          for _, h in szs]
            self._line = H / 2
        self._xoff = []
        if self.slots:
            # Spacer fica FORA do ritmo: ocupa a própria largura, sem virar slot
            # (Spacer no max das larguras inflava todos os slots — bug confirmado)
            real = [w for c, (w, _) in zip(self.children, szs) if not isinstance(c, Spacer)]
            SW = max(real) if real else 0
            x = 0
            for c, (cw, _) in zip(self.children, szs):
                if isinstance(c, Spacer):
                    self._xoff.append(x)
                    x += cw
                else:
                    self._xoff.append(x + (SW - cw) / 2)
                    x += SW + self.gap
            W = x - (self.gap if real and not isinstance(self.children[-1], Spacer) else 0)
        else:
            x = 0
            for cw, _ in szs:
                self._xoff.append(x)
                x += cw + self.gap
            W = x - self.gap
        return (W, H)

    def _arrange(self):
        super()._arrange()
        if self.align_x:
            node, ref = self.align_x
            if ref.frame is None:
                raise ConstructionError(
                    f"align_x: a referência ({type(ref).__name__}) ainda não foi posicionada — "
                    "align_x só aceita nó de seção ANTERIOR (referência para trás)")
            dx = ref.cx - node.cx
            for c in self.children:
                c._translate(dx)
            # frames do próprio HStack e dos ancestrais acompanham o deslocamento
            # (frame stale enganava overlays e validações — bug confirmado)
            if self.children:
                cx1 = min(c.x for c in self.children)
                cx2 = max(c.right for c in self.children)
                n = self
                while n is not None and n.frame is not None:
                    x, y, w, h = n.frame
                    x1, x2 = min(x, cx1), max(x + w, cx2)
                    n.frame = (x1, y, x2 - x1, h)
                    n = n.parent


class Grid(Node):
    """grade com células uniformes (ritmo da regra 1 em 2D), conteúdo centrado."""
    def __init__(self, *children, cols=4, gapx=40, gapy=36, name=None):
        super().__init__(name)
        self.children, self.cols = list(children), cols
        self.gapx, self.gapy = gapx, gapy

    def _measure(self):
        szs = [c.measure() for c in self.children]
        if not szs:
            return (0, 0)
        cw, ch = max(w for w, _ in szs), max(h for _, h in szs)
        n = len(szs)
        ncols = min(n, self.cols)
        nrows = (n + self.cols - 1) // self.cols
        self._xoff, self._yoff = [], []
        for i, (w, h) in enumerate(szs):
            r, c = divmod(i, self.cols)
            self._xoff.append(c * (cw + self.gapx) + (cw - w) / 2)
            self._yoff.append(r * (ch + self.gapy) + (ch - h) / 2)
        return (cw * ncols + self.gapx * (ncols - 1),
                ch * nrows + self.gapy * (nrows - 1))


class Lane(Node):
    """label de seção + row + rail AUTOMÁTICO (regras 2, 6, 7).
    O rail é emitido SOB os cards, da borda esquerda do 1º sólido à direita do
    último — derivado da geometria realizada; o autor nunca passa x1/x2."""
    def __init__(self, label, row, color=AZ, rail=True, gap=26, fs=20, name=None):
        super().__init__(name)
        self.label, self.row, self.color = label, row, color
        self.rail_on, self.gap, self.fs = rail, gap, fs
        self.children = [row]
        self._extra_gap = 0  # reservas ADITIVAS (brackets, attaches above)

    def _measure(self):
        lw, lh = measure(self.label, self.fs)
        rw, rh = self.row.measure()
        self._xoff, self._yoff = [0], [lh + self.gap + self._extra_gap]
        self._line = self._yoff[0] + self.row.line()
        return (max(lw, rw), self._yoff[0] + rh)

    @property
    def rail_y(self):
        return self.row.line_y

    def rail_span(self):
        """(x1, x2) do rail se ele existir — rail só faz sentido conectando
        2+ sólidos (trilho cortando uma Table sozinha violava a regra 6)."""
        boxes = list(self.row.solid_boxes())
        if self.rail_on and len(boxes) >= 2:
            return (min(b[0] for b in boxes), max(b[2] for b in boxes))
        return None

    def emit(self, d):
        d.text(self.uid + "_lbl", self.x, self.y, self.label, self.fs, self.color)
        span = self.rail_span()
        if span:
            d.rail(self.uid + "_rail", self.rail_y, span[0], span[1], self.color)
        self.row.emit(d)


# solid_boxes antes do arrange (p/ anchor auto no measure): versão estática
def _solid_boxes_static(self):
    if self.solid and not self.children:
        yield True
    for c in self.children:
        yield from c.solid_boxes_static()


Node.solid_boxes_static = _solid_boxes_static


# ============================== página ==============================
class Page:
    def __init__(self, title=None, margin=60, gap=56, max_w=None, autonum=False):
        if margin < 40:
            raise ConstructionError(f"Page: margin={margin} < 40 — a margem mínima do canvas é 40")
        self.title = title
        self.margin, self.gap, self.max_w, self.autonum = margin, gap, max_w, autonum
        self.root = VStack(gap=gap, align="left", name="root")
        self._arrows, self._brackets, self._notes, self._attaches = [], [], [], []
        self._artifacts = []   # (box, descrição) de overlays p/ validação
        self._struct = []      # labels de lane + título: visíveis a colisões
        self._rails = []       # bandas dos rails: obstáculo p/ notes/attaches
        self._decisions = []   # decisões heurísticas visíveis no dump

    def add(self, *nodes):
        self.root.children.extend(nodes)
        return nodes[0] if len(nodes) == 1 else nodes

    # ---- relações (por referência; resolvidas pós-layout) ----
    def arrow(self, src, dst, label=None, color=AZ, ss="solid", side="auto"):
        self._arrows.append((src, dst, label, color, ss, side))

    def bracket(self, nodes, label=None, color="#868e96"):
        self._brackets.append((list(nodes), label, color))

    def note(self, target, num, s, palette="laranja", side="auto", gap=50):
        if side not in ("auto", "right", "left", "below"):
            raise ConstructionError(f"note: side inválido '{side}' (use auto|right|left|below)")
        self._notes.append((target, num, s, _pal(palette), side, gap))

    def attach(self, node, target, side="above", gap=14):
        """posiciona um nó solto (ícone, texto) relativo a um nó do fluxo."""
        if side not in ("above", "below", "left", "right"):
            raise ConstructionError(f"attach: side inválido '{side}' (use above|below|left|right)")
        self._attaches.append((node, target, side, gap))

    # ---- pipeline ----
    def save(self, path, debug=False, strict=True):
        issues = []
        # reset total: save() é re-chamável e um measure() pré-save não congela
        # tamanhos sem as reservas (bugs confirmados no ataque adversarial)
        self.root._reset()
        for n, *_ in self._attaches:
            n._reset()
        self._artifacts, self._struct, self._rails, self._decisions = [], [], [], []
        self.root.freeze(None, "root")
        for i, (n, t, side, gap) in enumerate(self._attaches):
            n.freeze(None, f"attach{i}_{side}")
        self._apply_reserves()
        if self.autonum:
            lanes = [n for n in self.root.walk() if isinstance(n, Lane)]
            for i, ln in enumerate(lanes):
                base = getattr(ln, "_label_base", ln.label)
                ln._label_base = base
                ln.label = f"{i + 1}. {base}"

        d = Doc()
        y = 40
        if self.title:
            d.text("title", self.margin, y, self.title, 28, "#1e1e1e")
            tw, th = measure(self.title, 28)
            self._struct.append(((self.margin, y, self.margin + tw, y + th), "título"))
            y += th + 24
        self.root.measure()
        self.root.arrange(self.margin, y)

        # caixas estruturais: labels de lane e rails são emitidos fora da árvore
        # de nós, mas precisam ser visíveis a colisões (bug confirmado)
        for n in self.root.walk():
            if isinstance(n, Lane):
                lw, lh = measure(n.label, n.fs)
                self._struct.append(((n.x, n.y, n.x + lw, n.y + lh), f"{n.uid}_lbl"))
                span = n.rail_span()
                if span:
                    self._rails.append(((span[0], n.rail_y - 2, span[1], n.rail_y + 2),
                                        f"{n.uid}_rail"))

        if debug:
            self._ghosts(d)
        self.root.emit(d)
        self._resolve_attaches(d, issues)
        self._resolve_brackets(d, issues)
        self._resolve_notes(d, issues)
        self._resolve_arrows(d, issues)

        issues += self._check_overlap()
        issues += self._check_margins(d)
        rows = self._rows()
        issues += d.validate(rows)

        self._dump()
        if issues:
            report = "\n".join(f"  ✗ {m}" for m in issues)
            if strict:
                raise LayoutError(f"{len(issues)} violação(ões) — nada foi salvo:\n{report}\n"
                                  "  (strict=False salva mesmo assim, para inspecionar)")
            print(f"AVISOS ({len(issues)}):\n{report}")
        ok = d.save(path, rows=rows)
        return ok and not issues

    # ---- reservas (ANTES do measure; SOMAM, nunca max — regra 7 preservada) ----
    def _apply_reserves(self):
        def host_lane(node):
            p = node.parent
            while p is not None and not isinstance(p, Lane):
                p = p.parent
            return p

        def reserve_on(node, px):
            lane = host_lane(node)
            if lane is not None:
                lane._extra_gap += px
                self._decisions.append(f"reserva +{px:.0f}px no gap de {lane.uid}")
            else:
                sec = node
                while sec.parent is not None and sec.parent is not self.root:
                    sec = sec.parent
                sec._top_reserve += px
                self._decisions.append(f"reserva +{px:.0f}px acima de {sec.uid}")

        for nodes, label, _ in self._brackets:
            band = CLEAR_BRACKET + 12 + ((16 + measure(label, 13)[1]) if label else 0)
            reserve_on(nodes[0], band)
        for n, t, side, gap in self._attaches:
            if side == "above":
                reserve_on(t, n.measure()[1] + gap)

    # ---- overlays ----
    @staticmethod
    def _placed(n, ctx):
        if n.frame is None:
            raise ConstructionError(f"{ctx}: o nó ({type(n).__name__}) não está na página — "
                                    f"faltou p.add(...) dele ou de um ancestral?")

    def _obstacles(self, exclude=(), rails=False):
        # exclusão por FRONTEIRA de caminho: 'card1' não pode excluir 'card10'
        # (bug ALTA confirmado: seta atravessava card de índice >= 10)
        ex = tuple(n.uid for n in exclude if n.uid)
        def excluded(uid):
            return any(uid == e or uid.startswith(e + "/") for e in ex)
        boxes = [(b, u) for b, u in self.root.tangible_boxes() if not excluded(u)]
        boxes += self._struct
        boxes += [(b, desc) for b, desc in self._artifacts if not excluded(desc)]
        if rails:
            boxes += self._rails
        return boxes

    def _resolve_attaches(self, d, issues):
        for n, t, side, gap in self._attaches:
            self._placed(t, f"attach({side})")
            nw, nh = n.measure()
            pos = {"above": (t.cx - nw / 2, t.top - gap - nh),
                   "below": (t.cx - nw / 2, t.bottom + gap),
                   "left": (t.left - gap - nw, t.line_y - nh / 2),
                   "right": (t.right + gap, t.line_y - nh / 2)}[side]
            n.arrange(*pos)
            n.emit(d)
            box = (n.x, n.y, n.right, n.bottom)
            for ob, uid in self._obstacles(exclude=(t, n), rails=True):
                if _boxes_intersect(box, ob):
                    issues.append(f"attach {n.uid} ({side} de {t.uid}) sobrepõe {uid} — "
                                  f"aumente gap= ou mude side=")
            self._artifacts.append((box, n.uid))

    def _resolve_brackets(self, d, issues):
        prepared = []
        for i, (nodes, label, color) in enumerate(self._brackets):
            for n in nodes:
                self._placed(n, f"bracket('{label or i}')")
            tang = [b for n in nodes for b, _ in n.tangible_boxes()]
            if not tang:
                raise ConstructionError(f"bracket('{label or i}'): os nós não têm conteúdo "
                                        "tangível (Spacer/container vazio?)")
            solid = [b for n in nodes for b in n.solid_boxes()] or tang
            # span abraça as bordas dos CARDS (regra 4); a altura limpa TUDO
            # que é tangível no grupo (ícone acima do card, meta, etc.)
            x1, x2 = min(b[0] for b in solid), max(b[2] for b in solid)
            top = min(b[1] for b in tang)
            prepared.append((x2 - x1, i, label, color, x1, x2, top))
        # do span mais estreito ao mais largo: brackets aninhados EMPILHAM,
        # cada um limpando attaches/brackets já colocados no seu span
        for _, i, label, color, x1, x2, top in sorted(prepared, key=lambda t: t[0]):
            for (bx1, by1, bx2, _by2), _u in self._artifacts:
                if bx2 > x1 and bx1 < x2:
                    top = min(top, by1)
            yb = top - CLEAR_BRACKET
            d.bracket(f"bracket{i}", x1, x2, yb, color)
            band = (x1, yb, x2, yb + 12)
            if label:
                lh = measure(label, 13)[1]
                d.ctext(f"bracket{i}_t", (x1 + x2) / 2, yb - 16 - lh / 2, label, 13, color)
                band = (x1, yb - 16 - lh, x2, yb + 12)
            self._artifacts.append((band, f"bracket({label or i})"))
            self._decisions.append(f"bracket({label or i}): topo em y={yb:.0f}")

    def _resolve_notes(self, d, issues):
        for i, (t, num, s, pal, side, gap) in enumerate(self._notes):
            self._placed(t, f"note({num})")
            tw, th = measure(s, 14)
            w = 34 + 14 + tw
            h = max(34, th)  # box de colisão com a ALTURA REAL do texto
            cands = [("right", t.right + gap, t.line_y),
                     ("left", t.left - gap - w, t.line_y),
                     ("below", t.cx - w / 2, t.bottom + 16 + h / 2)]  # regra 3: sob o eixo
            if side != "auto":
                cands = [c for c in cands if c[0] == side]
            placed = False
            for sname, x, cy in cands:
                box = (x, cy - h / 2, x + w, cy + h / 2)
                if not any(_boxes_intersect(box, ob)
                           for ob, _ in self._obstacles(exclude=(t,), rails=True)):
                    d.badge_note(f"note{i}", x, cy, num, s, pal[0], pal[2])
                    self._artifacts.append((box, f"note{num}"))
                    self._decisions.append(f"note {num}: {sname} de {t.uid}")
                    placed = True
                    break
            if not placed:
                issues.append(f"note {num} ('{s[:30]}…') não tem posição livre ao redor de {t.uid} "
                              f"(tentei right/left/below) — aumente o gap do HStack ou ancore em outro nó")

    def _resolve_arrows(self, d, issues):
        for a, b, *_ in self._arrows:
            self._placed(a, "arrow (origem)")
            self._placed(b, "arrow (destino)")
        order = sorted(range(len(self._arrows)),
                       key=lambda i: (self._arrows[i][0].y, self._arrows[i][0].x))
        for i in order:
            a, b, label, color, ss, side = self._arrows[i]
            obstacles = self._obstacles(exclude=(a, b))
            segs = None
            if abs(a.cx - b.cx) < TOL:
                x = a.cx
                y1, y2 = (a.bottom + GAP_ARROW, b.top - GAP_ARROW) if b.top >= a.bottom \
                    else (a.top - GAP_ARROW, b.bottom + GAP_ARROW)
                segs = [(x, y1, x, y2)]
                d.varrow(f"arrow{i}", x, y1, y2, color, ss=ss)
                self._decisions.append(f"arrow {a.uid}->{b.uid}: vertical pura")
            elif abs(a.line_y - b.line_y) < TOL:
                yy = a.line_y
                x1, x2 = (a.right + GAP_ARROW, b.left - GAP_ARROW) if b.left >= a.right \
                    else (a.left - GAP_ARROW, b.right + GAP_ARROW)
                segs = [(x1, yy, x2, yy)]
                d.harrow(f"arrow{i}", yy, x1, x2, color, ss=ss)
                self._decisions.append(f"arrow {a.uid}->{b.uid}: horizontal pura")
            elif abs(a.cx - b.cx) < MIN_JOG:
                issues.append(f"arrow {a.uid}->{b.uid}: desalinhados por só {abs(a.cx - b.cx):.0f}px "
                              f"(quase-alinhados; cotovelo seria ilegível) — alinhe as colunas com "
                              f"HStack(align_x=(nó_desta_row, nó_da_row_de_referência))")
                continue
            else:
                down = b.cy >= a.cy
                if b.left - INFL < a.cx < b.right + INFL:
                    issues.append(f"arrow {a.uid}->{b.uid}: a origem está sobre o corpo do destino "
                                  f"(o cotovelo entraria por dentro do card) — alinhe os centros com "
                                  f"HStack(align_x=...) para virar seta vertical pura")
                    continue
                y1 = a.bottom + GAP_ARROW if down else a.top - GAP_ARROW
                ok_dir = (b.line_y >= a.bottom + MIN_JOG) if down else (b.line_y <= a.top - MIN_JOG)
                xe = b.left - GAP_ARROW if b.cx >= a.cx else b.right + GAP_ARROW
                if not ok_dir or abs(xe - a.cx) < MIN_JOG:
                    issues.append(f"arrow {a.uid}->{b.uid}: não há rota V/H legível "
                                  f"(segmento < {MIN_JOG}px ou retorno sobre a origem) — "
                                  f"alinhe os eixos com align_x ou aproxime as seções")
                    continue
                segs = [(a.cx, y1, a.cx, b.line_y), (a.cx, b.line_y, xe, b.line_y)]
                e = d.line(f"arrow{i}", a.cx, y1,
                           [[0, 0], [0, b.line_y - y1], [xe - a.cx, b.line_y - y1]], color, 2, ss)
                e["type"] = "arrow"
                e["endArrowhead"] = "arrow"
                self._decisions.append(f"arrow {a.uid}->{b.uid}: cotovelo V/H")
            hit = set()
            for (sx1, sy1, sx2, sy2) in segs or []:
                for ob, uid in obstacles:
                    if uid not in hit and _seg_hits_box(sx1, sy1, sx2, sy2, ob):
                        hit.add(uid)
                        issues.append(f"arrow {a.uid}->{b.uid} atravessa {uid} — "
                                      f"reordene as seções ou conecte nós sem obstáculo entre eles")
            if label and segs:
                self._place_arrow_label(d, i, segs, label, color, side, issues)

    def _place_arrow_label(self, d, i, segs, label, color, side, issues):
        sx1, sy1, sx2, sy2 = max(segs, key=lambda s: abs(s[3] - s[1]) + abs(s[2] - s[0]))
        lw, lh = measure(label, 12)
        vertical = abs(sx1 - sx2) < 0.01
        cands = []
        for t in (0.5, 0.33, 0.67):
            my = sy1 + (sy2 - sy1) * t
            mx = sx1 + (sx2 - sx1) * t
            if vertical:
                cands += [("direita", sx1 + 24, my - lh / 2), ("esquerda", sx1 - 24 - lw, my - lh / 2)]
            else:
                cands += [("acima", mx - lw / 2, sy1 - 14 - lh), ("abaixo", mx - lw / 2, sy1 + 14)]
        if side in ("left", "esquerda"):
            cands = [c for c in cands if c[0] == "esquerda"] + [c for c in cands if c[0] != "esquerda"]
        for sname, x, ytop in cands:
            box = (x, ytop, x + lw, ytop + lh)
            if not any(_boxes_intersect(box, ob, tol=0.5) for ob, _ in self._obstacles()):
                d.text(f"arrow{i}_l", x, ytop, label, 12, color)
                self._artifacts.append((box, f"label('{label[:20]}')"))
                self._decisions.append(f"label da arrow{i}: {sname} @t={((ytop + lh / 2 - sy1) / (sy2 - sy1)) if vertical and sy2 != sy1 else 0.5:.2f}")
                return
        issues.append(f"label de seta '{label[:30]}' colide em TODOS os 6 candidatos — "
                      f"encurte o texto ou aumente os gaps ao redor")

    # ---- validações (re-medidas, não tautológicas) ----
    def _check_overlap(self):
        out = []
        boxes = (list(self.root.tangible_boxes()) + [(b, u) for b, u in self._artifacts]
                 + self._struct)
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (b1, u1), (b2, u2) = boxes[i], boxes[j]
                if _boxes_intersect(b1, b2):
                    out.append(f"sobreposição: {u1} × {u2} "
                               f"({min(b1[2], b2[2]) - max(b1[0], b2[0]):.0f}px de invasão)")
        return out

    def _check_margins(self, d):
        out = []
        xs1, xs2 = [], []
        for e in d.els:
            if "__debug__" in (e.get("groupIds") or []):
                continue  # ghosts de debug não entram no orçamento (bug confirmado)
            if e.get("points"):
                # linha/seta: x é o ponto de PARTIDA, não o canto do bbox —
                # points podem ser negativos (seta direita->esquerda)
                pxs = [p[0] for p in e["points"]]
                xs1.append(e["x"] + min(pxs))
                xs2.append(e["x"] + max(pxs))
            else:
                xs1.append(e["x"])
                xs2.append(e["x"] + e["width"])
        xs1 += [b[0] for b, _ in self._artifacts]
        xs2 += [b[2] for b, _ in self._artifacts]
        if xs1 and min(xs1) < 40:
            out.append(f"conteúdo invade a margem esquerda do canvas (x={min(xs1):.0f} < 40) — "
                       f"verifique attaches/notes à esquerda do primeiro nó")
        if self.max_w and xs2 and max(xs2) > self.max_w:
            widest = max((n for n in self.root.walk() if isinstance(n, (Card, Text, Zone))),
                         key=lambda n: n.w, default=None)
            hint = f" — candidato a wrap: {widest.uid} (w={widest.w:.0f}px, tente wrap={int(widest.w * 0.6)})" if widest else ""
            out.append(f"página estoura max_w={self.max_w} (chega a {max(xs2):.0f}px){hint}")
        return out

    def _rows(self):
        # só os cards cuja LINHA está no rail (protocolo de anchor: num
        # VStack(Card, Card) apenas o card-âncora fica no rail — correto)
        rows = {}
        for n in self.root.walk():
            if isinstance(n, Lane) and n.rail_span():
                ids = [c.uid for c in n.row.walk()
                       if isinstance(c, (Card, Zone)) and abs(c.line_y - n.rail_y) < 0.6]
                if ids:
                    rows[n.uid] = (n.rail_y, ids)
        return rows

    # ---- ferramentas de inspeção ----
    def _ghosts(self, d):
        for n in self.root.walk():
            if n.children:
                g = d._base("rectangle", f"dbg_{n.uid.replace('/', '_')}", n.x, n.y,
                            n.w, n.h, "#be4bdb", "transparent", 1, "dashed")
                g["opacity"] = 30
                g["groupIds"] = ["__debug__"]
                d.els.append(g)
                t = d._base("line", f"dbgl_{n.uid.replace('/', '_')}", n.x - 10, n.line_y,
                            8, 0, "#be4bdb", sw=1, rough=0)
                t.update({"points": [[0, 0], [8, 0]], "roundness": None,
                          "lastCommittedPoint": None, "startBinding": None,
                          "endBinding": None, "startArrowhead": None, "endArrowhead": None})
                t["opacity"] = 30
                t["groupIds"] = ["__debug__"]
                d.els.append(t)

    def _dump(self):
        def line(n, depth):
            fr = f"({n.x:.0f},{n.y:.0f} {n.w:.0f}×{n.h:.0f})" if n.frame else "(sem frame)"
            print("  " * depth + f"{n.uid.split('/')[-1]} {fr} line_y={n.line_y:.0f}"
                  if n.frame else "  " * depth + n.uid)
            for c in n.children:
                line(c, depth + 1)
        line(self.root, 0)
        for dec in self._decisions:
            print(f"  · {dec}")


__all__ = ["Page", "Lane", "VStack", "HStack", "Grid", "Card", "Conclusion", "Zone",
           "Text", "Icon", "Chips", "Table", "Graph", "Sketch", "Spacer",
           "PALETAS", "AZ", "ConstructionError", "LayoutError", "wrap_text"]


if __name__ == "__main__":
    # selftest mínimo: uma página com cada mecanismo, salva em /tmp
    p = Page("Selftest excalidraw_dom", max_w=1600)
    p.add(Chips([("ok", "verde"), ("atenção", "amarelo")]))
    c1 = Card("primeiro\ncard", "amarelo", meta="meta sob o card")
    c2 = VStack(Icon("raio", color="#e8a500", fill="#fff3bf"), Card("com ícone\nem cima", "laranja"))
    c3 = Card("crítico", "vermelho", critical=True)
    lane1 = p.add(Lane("Linha um", HStack(c1, c2, c3)))
    c4 = Card("destino", "verde")
    # alinha com a coluna do MEIO (não a primeira: a seta cruzaria o label da lane)
    p.add(Lane("Linha dois", HStack(c4, Card("vizinho", "cinza"), align_x=(c4, c2))))
    p.add(HStack(Graph(fn=lambda t: t * t, caption="parábola", ylabel="S", xlabel="t", color="#2f9e44"),
                 Table(["a", "b"], [["1", "2"]]), slots=False, gap=80))
    p.add(Conclusion("conclusão do selftest"))
    p.arrow(c2, c4, label="vertical pura")
    p.note(c3, 1, "nota lateral")
    p.bracket([c1, c2], "grupo")
    assert p.save("/tmp/selftest_dom.excalidraw", debug=True)
    print("SELFTEST OK")
