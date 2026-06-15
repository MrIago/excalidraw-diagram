"""Regressão dos 20 bugs únicos confirmados pelo ataque adversarial."""
import sys
sys.path.insert(0, "/home/mriago/.claude/skills/excalidraw-diagram/scripts")
from excalidraw_dom import *
import io, contextlib

PASS, FAIL = [], []


def check(nome, fn):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        PASS.append(nome)
    except AssertionError as e:
        FAIL.append(f"{nome}: {e}")
    except Exception as e:
        FAIL.append(f"{nome}: {type(e).__name__}: {e}")


def expect_issue(p, frag):
    try:
        p.save("/tmp/r.excalidraw")
        raise AssertionError(f"era para falhar com '{frag}'")
    except LayoutError as e:
        assert frag in str(e), f"falhou, mas sem '{frag}': {e}"


def expect_construction(fn, frag):
    try:
        fn()
        raise AssertionError(f"era para dar ConstructionError com '{frag}'")
    except ConstructionError as e:
        assert frag in str(e), f"erro errado: {e}"


# [0/8/27] exclusão por fronteira de caminho: card1 não exclui card10
def t_prefix():
    p = Page()
    cards = [Card(f"c{i}", "cinza") for i in range(11)]
    lane = p.add(Lane("L", HStack(*cards, gap=20)))
    alvo = p.add(Lane("M", HStack(Card("alvo", "verde")), rail=False))
    # seta do card1 ao alvo desalinhado: passa por cima de cards vizinhos? força cotovelo
    p.arrow(cards[1], alvo.row.children[0])
    try:
        p.save("/tmp/r.excalidraw")
    except LayoutError as e:
        assert "atravessa" in str(e) or "rota V/H" in str(e) or "origem está sobre" in str(e)


# [15] origem sobre o corpo do destino -> erro, nunca cotovelo por dentro
def t_corner_inside():
    p = Page()
    a = Card("origem", "cinza")
    b = Card("destino largo                                 x", "verde")
    p.add(Lane("A", HStack(a), rail=False))
    p.add(Lane("B", HStack(b), rail=False))
    # desalinha um pouco para não ser vertical pura, mas a.cx cai sobre o span de b
    p.add(Lane("C", HStack(Card("enchimento de largura                    .", "cinza")), rail=False))
    p.arrow(a, b)
    expect_issue(p, "origem está sobre o corpo do destino")


# [16] quase-alinhamento em Y -> erro de rota, nunca cotovelo de retorno
def t_y_quasi():
    p = Page()
    a = Card("aaaa", "cinza")
    b = Card("bb\nbb\nbb", "verde")   # alturas diferentes -> line_y desalinha poucos px
    p.add(Lane("A", HStack(a, Card("meio", "cinza"), b, gap=60)))
    p.arrow(a, b)
    try:
        p.save("/tmp/r.excalidraw")
    except LayoutError as e:
        assert "rota V/H" in str(e) or "atravessa" in str(e), e


# [17/28] label de lane é obstáculo: seta vertical cruzando lane intermediária
def t_label_obstacle():
    p = Page()
    a = Card("origem  do fluxo geral", "cinza")   # mesmo nº de chars que o destino
    p.add(Lane("Primeira", HStack(a), rail=False))
    p.add(Lane("Label comprido da lane intermediária que a seta cruzaria", HStack(Card("x", "cinza")), rail=False))
    b = Card("destino do fluxo geral", "verde")
    p.add(Lane("Terceira", HStack(b), rail=False))
    p.arrow(a, b)
    expect_issue(p, "atravessa")


# [23] attaches duplicados no mesmo alvo/lado: ids únicos
def t_attach_dup():
    p = Page()
    c = Card("alvo", "cinza")
    p.add(Lane("L", HStack(c), rail=False))
    p.attach(Icon("check", color="#2f9e44"), c, side="right", gap=14)
    p.attach(Icon("x", color="#e03131"), c, side="right", gap=60)
    p.save("/tmp/r_dup.excalidraw", strict=False)
    import json
    els = json.load(open("/tmp/r_dup.excalidraw"))["elements"]
    ids = [e["id"] for e in els]
    assert len(ids) == len(set(ids)), "ids duplicados no JSON"


# [5] measure() pré-save não descarta reservas
def t_premeasure():
    p = Page()
    c1, c2 = Card("a", "cinza"), Card("b", "verde")
    lane = p.add(Lane("L", HStack(c1, c2)))
    p.bracket([c1], "grupo")
    lane.measure()  # autor mediu antes — não pode congelar sem reserva
    assert p.save("/tmp/r2.excalidraw")
    assert lane._extra_gap > 0, "reserva do bracket foi descartada"


# [25/32] save() re-chamável
def t_resave():
    p = Page("T")
    p.add(Lane("L", HStack(Card("a", "cinza"), Card("b", "verde"))))
    assert p.save("/tmp/r3.excalidraw")
    assert p.save("/tmp/r3.excalidraw")  # segunda chamada


# autonum não duplica prefixo no re-save
def t_autonum_resave():
    p = Page("T", autonum=True)
    lane = p.add(Lane("Único", HStack(Card("a", "cinza"), Card("b", "verde"))))
    p.save("/tmp/r4.excalidraw")
    p.save("/tmp/r4.excalidraw")
    assert lane.label == "1. Único", lane.label


# [2/19] bracket + attach above no mesmo grupo: empilham, salvam OK
def t_bracket_attach():
    p = Page()
    c1, c2 = Card("um", "cinza"), Card("dois", "verde")
    p.add(Lane("L", HStack(c1, c2)))
    p.attach(Icon("alert", color="#e8590c"), c1, side="above")
    p.bracket([c1, c2], "grupo")
    assert p.save("/tmp/r5.excalidraw"), "bracket+attach deveria salvar"


# [20] dois brackets aninhados empilham
def t_two_brackets():
    p = Page()
    c1, c2, c3 = Card("um", "cinza"), Card("dois", "verde"), Card("três", "amarelo")
    p.add(Lane("L", HStack(c1, c2, c3)))
    p.bracket([c1, c2], "interno")
    p.bracket([c1, c2, c3], "externo")
    assert p.save("/tmp/r6.excalidraw"), "brackets aninhados deveriam empilhar"


# [3/9/26] Graph mede labels: dois gráficos lado a lado sem invasão
def t_graph_labels():
    p = Page()
    g1 = Graph(fn=lambda t: t, ylabel="taxa de sucesso", xlabel="tempo decorrido")
    g2 = Graph(fn=lambda t: 1 - t, ylabel="erros", xlabel="t")
    p.add(HStack(g1, g2, slots=False, gap=10))
    assert p.save("/tmp/r7.excalidraw"), "labels medidos não deveriam colidir"


# [10] Spacer fora do ritmo de slots
def t_spacer_slots():
    p = Page()
    row = HStack(Card("aa", "cinza"), Spacer(w=400, h=1), Card("bb", "verde"))
    p.add(Lane("L", row, rail=False))
    p.save("/tmp/r8.excalidraw")
    assert row.w < 600, f"Spacer inflou os slots: row.w={row.w}"


# [6] note multi-linha tem box real / [29] candidato below
def t_note_below():
    p = Page()
    a, b, c = Card("um", "cinza"), Card("dois", "amarelo"), Card("três", "verde")
    p.add(Lane("L", HStack(a, b, c, gap=30)))
    p.note(b, 1, "nota que não cabe dos lados")
    assert p.save("/tmp/r9.excalidraw")


# [7] rail não corta Table sozinha
def t_rail_table():
    p = Page()
    p.add(Lane("L", Table(["a", "b"], [["1", "2"]])))
    p.save("/tmp/r10.excalidraw")
    import json
    els = json.load(open("/tmp/r10.excalidraw"))["elements"]
    assert not any(e["id"].endswith("_rail") for e in els), "rail emitido p/ sólido único"


# [11..14, 21, 22, 30, 31, 34] guardas de construção
def t_guards():
    expect_construction(lambda: Chips([]), "vazia")
    expect_construction(lambda: Graph(fn=lambda t: t, n=1), "2 pontos")
    expect_construction(lambda: Page(margin=10), "margem mínima")
    expect_construction(lambda: Page().attach(Icon("check"), Card("x"), side="acima"), "side inválido")
    expect_construction(lambda: Page().note(Card("x"), 1, "s", side="cima"), "side inválido")
    expect_construction(lambda: HStack(align="middle"), "align inválido")
    expect_construction(lambda: VStack(align="top"), "align inválido")
    p = Page()
    p.add(Lane("L", HStack(Card("a", "cinza"), Card("b", "verde"))))
    orfao = Card("nunca adicionado", "vermelho")
    p.arrow(orfao, Card("outro", "cinza"))
    expect_construction(lambda: p.save("/tmp/r11.excalidraw"), "não está na página")


# [1] harrow direita->esquerda não gera falso estouro de margem
def t_arrow_bbox():
    p = Page(max_w=900)
    a, b = Card("esquerda", "cinza"), Card("direita", "verde")
    p.add(Lane("L", HStack(a, b, gap=200)))
    p.arrow(b, a)  # seta de volta, points negativos
    assert p.save("/tmp/r12.excalidraw"), "falso positivo de margem com points negativos"


# [4] align_x expande frames dos ancestrais
def t_alignx_frames():
    p = Page()
    ref = Card("referência bem à direita para deslocar bastante", "cinza")
    p.add(Lane("A", HStack(Card("enchimento", "cinza"), ref, gap=80)))
    alvo = Card("alvo", "verde")
    row = HStack(alvo, align_x=(alvo, ref))
    lane2 = p.add(Lane("B", row, rail=False))
    p.save("/tmp/r13.excalidraw")
    assert row.right >= alvo.right - 0.5, "frame do HStack não acompanhou o translate"
    assert lane2.right >= alvo.right - 0.5, "frame da Lane não acompanhou"


for nome, fn in sorted({k[2:]: v for k, v in list(globals().items())
                        if k.startswith("t_")}.items()):
    check(nome, fn)

print(f"\n{len(PASS)} passaram, {len(FAIL)} falharam")
for f in FAIL:
    print("  FALHOU:", f)
sys.exit(1 if FAIL else 0)
