"""Layout engine para gerar arquivos .excalidraw — pense HTML/CSS, renderize Excalidraw.

Princípio: NENHUMA coordenada é chutada. Tudo deriva de:
  measure (texto medido) -> box (conteúdo + padding) -> slot/row (centro) -> cursor (fluxo vertical)

Uso (num script gerador descartável):
    import sys; sys.path.insert(0, "<skill_dir>/scripts")
    from excalidraw_engine import Doc
    d = Doc()
    d.title("Meu diagrama")
    ...
    d.save("/caminho/arquivo.excalidraw")   # valida e grava; stdout é o resultado

Convenções (regras de design do mriago — ver references/design-rules.md):
  - card() recebe o CENTRO (cx, cy), não o canto: shrink-to-fit centrado no slot/rail
  - rails nascem na borda do primeiro card e morrem na borda do último
  - linhas estruturais com roughness 0 (roughness 1 desenha traço duplo)
  - setas verticais puras (varrow), centro a centro
  - anotações (badge_note) na row do elemento que comentam, centros alinhados
  - cursor vertical: d.y avança com d.advance(px) — nada de y absoluto hardcoded
"""
import json

CHAR_W = 0.62   # monospace (fontFamily 3): largura ~0.62 * fontSize por caractere
LH = 1.25       # line-height do Excalidraw


def measure(s, fs):
    """(largura, altura) de um texto multilinhas em fontFamily 3."""
    ls = s.split("\n")
    return max(len(l) for l in ls) * fs * CHAR_W, len(ls) * fs * LH


def table_size(header, rows, fs=14, padx=14, pady=10):
    """(larguras de coluna, altura de linha, W, H) de uma tabela — fonte única
    da matemática de colunas, usada por Doc.table e pela camada de layout."""
    cols = list(zip(header, *rows))
    cw = [max(measure(str(c), fs)[0] for c in col) + 2 * padx for col in cols]
    rh = measure("X", fs)[1] + 2 * pady
    return cw, rh, sum(cw), rh * (len(rows) + 1)


class Doc:
    def __init__(self, bg="#ffffff"):
        self.els, self.bg = [], bg
        self._seed = 1000
        self.y = 40          # cursor vertical (estilo `linha += lsizey` do graphics.h)

    # ---------- infra ----------
    def _base(self, t, id, x, y, w, h, stroke, bgc="transparent", sw=2, ss="solid", rough=1):
        self._seed += 2
        return {"type": t, "id": id, "x": round(x, 1), "y": round(y, 1),
                "width": round(w, 1), "height": round(h, 1), "angle": 0,
                "strokeColor": stroke, "backgroundColor": bgc, "fillStyle": "solid",
                "strokeWidth": sw, "strokeStyle": ss, "roughness": rough, "opacity": 100,
                "seed": self._seed, "version": 1, "versionNonce": self._seed + 1,
                "isDeleted": False, "groupIds": [], "frameId": None, "boundElements": None,
                "updated": 1, "link": None, "locked": False}

    def advance(self, px):
        """avança o cursor vertical e o retorna."""
        self.y += px
        return self.y

    # ---------- primitivas ----------
    def text(self, id, x, y, s, fs, color, container=None, cy=None, align="left"):
        """texto livre; cy centraliza verticalmente nessa linha de centro."""
        w, h = measure(s, fs)
        if cy is not None:
            y = cy - h / 2
        e = self._base("text", id, x, y, w, h, color)
        e.update({"fontSize": fs, "fontFamily": 3, "text": s, "originalText": s,
                  "textAlign": align, "verticalAlign": "middle" if container else "top",
                  "containerId": container, "lineHeight": LH, "roundness": None})
        self.els.append(e)
        return e

    def ctext(self, id, cx, cy, s, fs, color):
        """texto livre centralizado em (cx, cy)."""
        w, h = measure(s, fs)
        return self.text(id, cx - w / 2, cy - h / 2, s, fs, color, align="center")

    def card(self, id, cx, cy, s, fs=14, stroke="#1e1e1e", fill="#ffffff",
             txt=None, padx=18, pady=16, sw=2, ss="solid"):
        """retângulo shrink-to-fit com texto contido, CENTRADO em (cx, cy)."""
        tw, th = measure(s, fs)
        w, h = tw + 2 * padx, th + 2 * pady
        r = self._base("rectangle", id, cx - w / 2, cy - h / 2, w, h, stroke, fill, sw, ss)
        r["roundness"] = {"type": 3}
        r["boundElements"] = [{"type": "text", "id": id + "_t"}]
        self.els.append(r)
        self.text(id + "_t", cx - tw / 2, 0, s, fs, txt or stroke, container=id, cy=cy)
        return r

    def card_w(self, s, fs=14, padx=18):
        """largura que card() dará a esse conteúdo (p/ derivar rails, brackets, zonas)."""
        return measure(s, fs)[0] + 2 * padx

    def line(self, id, x, y, pts, color, sw=2, ss="solid"):
        """linha estrutural (rail, divisor) — sempre roughness 0."""
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        e = self._base("line", id, x, y, max(xs) - min(xs), max(ys) - min(ys),
                       color, sw=sw, ss=ss, rough=0)
        e.update({"points": pts, "roundness": None, "lastCommittedPoint": None,
                  "startBinding": None, "endBinding": None,
                  "startArrowhead": None, "endArrowhead": None})
        self.els.append(e)
        return e

    def rail(self, id, y, x1, x2, color, ss="solid"):
        """trilho horizontal de x1 a x2 — passe as BORDAS do 1º e último card."""
        return self.line(id, x1, y, [[0, 0], [x2 - x1, 0]], color, 2, ss)

    def _arrow_label(self, a, s, fs, color, mode):
        """label de seta SEM sobrepor a linha (regra do mriago):
        - "beside" (PADRÃO) / "left": texto livre ao lado, 24px de folga, centrado
          no ponto médio — à direita ou à esquerda da seta vertical (escolha o lado
          com espaço livre); acima quando horizontal.
        - "bound": texto nativo dentro da seta — EVITAR; o usuário prefere ao lado.
        """
        pts = a["points"]
        mx = a["x"] + (pts[0][0] + pts[-1][0]) / 2
        my = a["y"] + (pts[0][1] + pts[-1][1]) / 2
        w, h = measure(s, fs)
        if mode == "bound":
            a["boundElements"] = [{"type": "text", "id": a["id"] + "_l"}]
            self.text(a["id"] + "_l", mx - w / 2, my - h / 2, s, fs, color,
                      container=a["id"], align="center")
        else:
            vertical = abs(pts[-1][0] - pts[0][0]) < abs(pts[-1][1] - pts[0][1])
            if vertical:
                x = a["x"] - 24 - w if mode == "left" else a["x"] + 24
                self.text(a["id"] + "_l", x, my - h / 2, s, fs, color, align="center")
            else:
                self.text(a["id"] + "_l", mx - w / 2, a["y"] - h - 12, s, fs, color, align="center")

    def varrow(self, id, x, y1, y2, color, ss="solid",
               label=None, label_mode="beside", label_fs=12, label_color=None):
        """seta vertical pura de (x,y1) -> (x,y2), no eixo do elemento.
        label NUNCA sobrepõe a linha: "beside" (ao lado) ou "bound" (dentro, nativo)."""
        e = self.line(id, x, y1, [[0, 0], [0, y2 - y1]], color, 2, ss)
        e["type"] = "arrow"
        e["endArrowhead"] = "arrow"
        if label:
            self._arrow_label(e, label, label_fs, label_color or color, label_mode)
        return e

    def harrow(self, id, y, x1, x2, color, ss="solid",
               label=None, label_mode="beside", label_fs=12, label_color=None):
        """seta horizontal pura de (x1,y) -> (x2,y). label: ver varrow."""
        e = self.line(id, x1, y, [[0, 0], [x2 - x1, 0]], color, 2, ss)
        e["type"] = "arrow"
        e["endArrowhead"] = "arrow"
        if label:
            self._arrow_label(e, label, label_fs, label_color or color, label_mode)
        return e

    def badge_note(self, id, x, cy, num, s, badge_color, text_color, fs=14):
        """badge numerado + texto lado a lado, centros verticais alinhados em cy.
        Retorna a borda direita do grupo. Para centralizar o grupo sob um eixo:
        x = eixo - (34 + 14 + measure(s, fs)[0]) / 2"""
        d = 34
        b = self._base("ellipse", id + "_b", x, cy - d / 2, d, d, badge_color, badge_color)
        b["roundness"] = None
        self.els.append(b)
        nw, _ = measure(str(num), 17)
        self.text(id + "_n", x + d / 2 - nw / 2, 0, str(num), 17, "#ffffff", cy=cy)
        self.text(id + "_f", x + d + 14, 0, s, fs, text_color, cy=cy)
        return x + d + 14 + measure(s, fs)[0]

    def bracket(self, id, x1, x2, y, color, depth=12, up=True):
        """colchete horizontal abraçando de x1 a x2 (pontas viradas p/ baixo se up)."""
        d = depth if up else -depth
        return self.line(id, x1, y, [[0, d], [0, 0], [x2 - x1, 0], [x2 - x1, d]], color, 2)

    def chips(self, items, x, cy, fs=13, gap=16):
        """fileira de chips [(label, stroke, fill, txt_color)] — legenda."""
        for i, (nm, st, fi, tx) in enumerate(items):
            w = self.card_w(nm, fs, 14)
            self.card(f"chip{i}", x + w / 2, cy, nm, fs, st, fi, txt=tx, padx=14, pady=8)
            x += w + gap
        return x

    def zone(self, id, x1, x2, cy, h, stroke, fill, s=None, txt="#1e1e1e", fs=14):
        """zona de destaque com fundo preenchido, centrada verticalmente em cy."""
        g = self._base("rectangle", id, x1, cy - h / 2, x2 - x1, h, stroke, fill, 1, "dashed")
        g["roundness"] = {"type": 3}
        self.els.append(g)
        if s:
            self.ctext(id + "_t", (x1 + x2) / 2, cy, s, fs, txt)
        return g

    def freedraw(self, id, x, y, pts, color, sw=2):
        """traço a LÁPIS (ferramenta freedraw do Excalidraw); pts relativos a (x,y).

        Use para curvas/desenhos à mão livre: gráficos (reta, parábola, seno),
        rabiscos, sublinhados ondulados — qualquer forma que você descreva
        ponto a ponto. Para parecer mão real, some uma tremida leve aos pontos:
            wob = lambda i: math.sin(i * 1.7) * 0.9   # amplitude ~1px
        Com simulatePressure o Excalidraw varia a espessura como caneta real.
        """
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        e = self._base("freedraw", id, x + min(xs), y + min(ys),
                       max(xs) - min(xs), max(ys) - min(ys), color, sw=sw)
        e.update({"points": [[px - min(xs), py - min(ys)] for px, py in pts],
                  "pressures": [], "simulatePressure": True,
                  "lastCommittedPoint": list(pts[-1]), "roundness": None})
        self.els.append(e)
        return e

    def sketch_axes(self, pid, ox, oy, w, h, color="#343a40"):
        """par de eixos x/y a lápis (origem no canto inferior esquerdo), com
        tremida leve — base para gráficos desenhados com freedraw()."""
        import math
        wob = lambda i: math.sin(i * 1.7) * 0.9
        self.freedraw(pid + "_ay", ox, oy, [(wob(i), -h * i / 14) for i in range(15)], color)
        self.freedraw(pid + "_ax", ox, oy, [(w * i / 14, wob(i)) for i in range(15)], color)

    # ---------- ícones a lápis ----------
    ICON_NAMES = ("check", "x", "alert", "gear", "clock", "person", "db", "doc", "lupa", "raio", "coracao")

    def icon(self, pid, name, cx, cy, s=34, color="#343a40", fill=None, fill_style="cross-hatch"):
        """glifo a lápis centrado em (cx, cy), caixa s×s. Nomes: ICON_NAMES.
        Traço SEMPRE sw=1 (testado: sw>=2 vira mancha em escala pequena).

        fill: cor de preenchimento p/ glifos de contorno fechado (coracao, raio,
        alert, doc, clock, lupa, gear, db) — None = vazado.
        fill_style: "cross-hatch" (padrão — preferido pelo usuário), "hachure", "solid".
        """
        import math
        wob = lambda i, a=0.5: math.sin(i * 1.7) * a

        def lerp(p, q, n=6):
            return [(p[0]+(q[0]-p[0])*i/n, p[1]+(q[1]-p[1])*i/n) for i in range(n+1)]

        def path(*pts, steps=6):
            out = []
            for a, b in zip(pts, pts[1:]):
                out += lerp(a, b, steps)
            return out

        def fillpoly(sub, pts):
            """polígono fechado preenchido SOB o traço de lápis (fillStyle do Excalidraw)."""
            if not fill:
                return
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, y0 = min(xs), min(ys)
            e = self._base("line", pid + sub + "_fill", x0, y0,
                           max(xs) - x0, max(ys) - y0, "transparent", fill, 1)
            e["fillStyle"] = fill_style
            rel = [[px - x0, py - y0] for px, py in pts]
            e.update({"points": rel + [rel[0]], "roundness": None, "lastCommittedPoint": None,
                      "startBinding": None, "endBinding": None,
                      "startArrowhead": None, "endArrowhead": None})
            self.els.append(e)

        def fd(sub, rel):
            pts = [(cx - s/2 + px*s + wob(i), cy - s/2 + py*s + wob(i+3))
                   for i, (px, py) in enumerate(rel)]
            self.freedraw(pid + sub, 0, 0, pts, color, 1)
            return pts

        def circle(sub, ccx, ccy, r, arc=(0, 2*math.pi), n=24):
            pts = [(ccx + r*math.cos(arc[0]+(arc[1]-arc[0])*i/n) + wob(i),
                    ccy + r*math.sin(arc[0]+(arc[1]-arc[0])*i/n) + wob(i+5)) for i in range(n+1)]
            self.freedraw(pid + sub, 0, 0, pts, color, 1)
            return pts

        absr = lambda rel: [(cx - s/2 + px*s, cy - s/2 + py*s) for px, py in rel]
        cpts = lambda ccx, ccy, r, n=24: [(ccx + r*math.cos(2*math.pi*i/n),
                                           ccy + r*math.sin(2*math.pi*i/n)) for i in range(n+1)]

        if name == "check":
            fd("", path((.08,.55),(.38,.85),(.92,.12)))
        elif name == "x":
            fd("a", path((.12,.12),(.88,.88))); fd("b", path((.88,.12),(.12,.88)))
        elif name == "alert":
            tri = path((.5,.05),(.95,.9),(.05,.9),(.5,.05))
            fillpoly("t", absr(tri))
            fd("t", tri)
            fd("l", path((.5,.32),(.5,.62), steps=4)); fd("d", path((.48,.76),(.52,.78), steps=2))
        elif name == "gear":
            fillpoly("c", cpts(cx, cy, s*.30))
            circle("c", cx, cy, s*.30); circle("i", cx, cy, s*.11)
            for k in range(8):
                a = k * math.pi / 4
                self.freedraw(f"{pid}t{k}", 0, 0,
                              lerp((cx + s*.30*math.cos(a), cy + s*.30*math.sin(a)),
                                   (cx + s*.46*math.cos(a), cy + s*.46*math.sin(a)), 3), color, 1)
        elif name == "clock":
            fillpoly("c", cpts(cx, cy, s*.45))
            circle("c", cx, cy, s*.45)
            self.freedraw(pid+"h", 0, 0, lerp((cx, cy), (cx, cy - s*.30), 4), color, 1)
            self.freedraw(pid+"m", 0, 0, lerp((cx, cy), (cx + s*.22, cy + s*.08), 4), color, 1)
        elif name == "person":
            circle("h", cx, cy - s*.22, s*.18)
            circle("b", cx, cy + s*.48, s*.42, arc=(math.pi*1.15, math.pi*1.85))
        elif name == "db":
            import math as _m
            ry, n = s*.14, 20
            # silhueta do cilindro: meia-elipse de cima + lados + arco de baixo
            sil = [(cx + s*.42*_m.cos(_m.pi + _m.pi*i/n), cy - s*.32 + ry*_m.sin(_m.pi + _m.pi*i/n)) for i in range(n+1)]
            sil += [(cx + s*.42, cy + s*.32)]
            sil += [(cx + s*.42*_m.cos(_m.pi*i/n), cy + s*.32 + ry*_m.sin(_m.pi*i/n)) for i in range(n+1)]
            sil += [(cx - s*.42, cy - s*.32)]
            fillpoly("s", sil)
            self.freedraw(pid+"e", 0, 0, [(cx + s*.42*_m.cos(2*_m.pi*i/n) + wob(i, .4),
                                           cy - s*.32 + ry*_m.sin(2*_m.pi*i/n) + wob(i+2, .4))
                                          for i in range(n+1)], color, 1)
            self.freedraw(pid+"l", 0, 0, lerp((cx - s*.42, cy - s*.32), (cx - s*.42, cy + s*.32), 5), color, 1)
            self.freedraw(pid+"r", 0, 0, lerp((cx + s*.42, cy - s*.32), (cx + s*.42, cy + s*.32), 5), color, 1)
            self.freedraw(pid+"b", 0, 0, [(cx + s*.42*_m.cos(_m.pi*i/n), cy + s*.32 + ry*_m.sin(_m.pi*i/n))
                                          for i in range(n+1)], color, 1)
        elif name == "doc":
            rect = path((.15,.05),(.7,.05),(.85,.2),(.85,.95),(.15,.95),(.15,.05))
            fillpoly("r", absr(rect))
            fd("r", rect)
            for j, yy in enumerate((.35,.5,.65)):
                fd(f"l{j}", path((.28,yy),(.72,yy), steps=3))
        elif name == "lupa":
            fillpoly("c", cpts(cx - s*.08, cy - s*.08, s*.28))
            circle("c", cx - s*.08, cy - s*.08, s*.28)
            self.freedraw(pid+"h", 0, 0, lerp((cx + s*.14, cy + s*.14), (cx + s*.4, cy + s*.4), 3), color, 1)
        elif name == "raio":
            bolt = path((.6,.05),(.3,.5),(.52,.5),(.4,.95),(.75,.42),(.52,.42),(.6,.05))
            fillpoly("", absr(bolt))
            fd("", bolt)
        elif name == "coracao":
            n = 28  # curva paramétrica do coração, um traço fechado
            pts = []
            for i in range(n + 1):
                t = 2 * math.pi * i / n
                hx = 16 * math.sin(t) ** 3
                hy = 13*math.cos(t) - 5*math.cos(2*t) - 2*math.cos(3*t) - math.cos(4*t)
                # hx ∈ [-16,16], hy ∈ [-17,12] (centro -2.5): normaliza p/ caixa s×s centrada em (cx,cy)
                pts.append((cx + s*.5*hx/16 + wob(i, .4), cy + s*.5*(-2.5 - hy)/14.5 + wob(i+4, .4)))
            fillpoly("", pts)
            self.freedraw(pid, 0, 0, pts, color, 1)
        else:
            raise ValueError(f"icon desconhecido: {name} (use {self.ICON_NAMES})")

    def table(self, pid, x, y, header, rows, fs=14, padx=14, pady=10,
              stroke="#1971c2", header_fill="#e7f5ff", header_txt="#1864ab", txt="#343a40"):
        """tabela com colunas medidas pelo conteúdo mais largo (+padding).
        Header com fill, grade interna sw=1, borda externa sw=2, células centradas.
        (x, y) é o canto superior esquerdo; retorna (largura, altura)."""
        cw, rh, W, H = table_size(header, rows, fs, padx, pady)
        self.els.append(self._base("rectangle", pid+"_hd", x, y, W, rh, stroke, header_fill, 2))
        self.els.append(self._base("rectangle", pid+"_o", x, y, W, H, stroke, "transparent", 2))
        for r in range(1, len(rows) + 1):
            self.line(f"{pid}_r{r}", x, y + r*rh, [[0, 0], [W, 0]], stroke, 1)
        cx = x
        for c in range(len(cw) - 1):
            cx += cw[c]
            self.line(f"{pid}_c{c}", cx, y, [[0, 0], [0, H]], stroke, 1)
        for r, row in enumerate([list(header)] + [list(r) for r in rows]):
            for c, cell in enumerate(row):
                self.ctext(f"{pid}_t{r}_{c}", x + sum(cw[:c]) + cw[c]/2, y + r*rh + rh/2,
                           str(cell), fs, header_txt if r == 0 else txt)
        return W, H

    def title(self, s, x=60, fs=28, color="#1e1e1e"):
        self.text("title", x, self.y, s, fs, color)
        return self.advance(measure(s, fs)[1] + 24)

    def lane(self, id, label, tallest_card_text, color, x=60, fs_label=20,
             gap=26, fs_card=14, pady=16):
        """Label de lane + cálculo seguro do rail.

        Posiciona o label no cursor atual e retorna o y do RAIL já descontando
        a meia-altura do card mais alto da row — assim o topo dos cards nunca
        invade o label (gap = espaço visual entre a base do label e o topo dos
        cards). Passe o CONTEÚDO do card mais alto da lane em tallest_card_text.
        Deixa o cursor no rail; depois da row, avance com:
            d.advance(card_h/2 + 14 + altura_da_meta + gap_de_seção)
        """
        self.text(id, x, self.y, label, fs_label, color)
        label_h = measure(label, fs_label)[1]
        card_h = measure(tallest_card_text, fs_card)[1] + 2 * pady
        rail_y = self.y + label_h + gap + card_h / 2
        self.y = rail_y
        return rail_y

    # ---------- validação + saída ----------
    def validate(self, rows=None):
        """rows: {nome: (cy, [ids])} — confere centros. Sempre confere overflow."""
        em = {e["id"]: e for e in self.els}
        issues = []
        for e in self.els:
            if e["type"] == "text" and e.get("containerId"):
                c = em[e["containerId"]]
                if c["type"] == "arrow":   # label bound em seta: o canvas abre o vão, não há overflow
                    continue
                tw, th = measure(e["text"], e["fontSize"])
                if tw > c["width"] - 6 or th > c["height"] - 6:
                    issues.append(f"overflow: {e['id']}")
        for name, (cy, ids) in (rows or {}).items():
            for i in ids:
                c = em[i]
                if abs(c["y"] + c["height"] / 2 - cy) > 0.6:
                    issues.append(f"{i} fora da row {name}")
        return issues

    def save(self, path, rows=None):
        issues = self.validate(rows)
        doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
               "elements": self.els,
               "appState": {"gridSize": None, "viewBackgroundColor": self.bg}, "files": {}}
        with open(path, "w") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(f"{len(self.els)} elementos -> {path}")
        print("validação:", "; ".join(issues) if issues else "OK")
        return not issues
