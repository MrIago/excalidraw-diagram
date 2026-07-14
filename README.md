# excalidraw-diagram

A Claude Code skill that generates .excalidraw diagrams through a declarative layout engine: describe a tree, get a measured, aligned, validated file. Neither you nor the model types a coordinate.

## What it does

Ask Claude for a flow, timeline, architecture, or comparison and the skill writes a short Python generator against a declarative API (`Page` > `Lane` > `HStack` > `Card`), runs it, and saves a valid `.excalidraw` file. You open it in the Excalidraw extension for VS Code or Obsidian. No renderer, no MCP server, no headless browser in the loop.

The problem it solves: LLMs are bad at absolute coordinates. Hand-placed x/y produces clipped text, overlapping arrows, and boxes that drift off their row. This engine removes coordinates from the interface, so the model spends its effort on the argument of the diagram (what goes in which lane, what deserves an annotation) and the layout math is computed, checked, and reported back.

```python
import sys; sys.path.insert(0, "<skill>/scripts")
from excalidraw_dom import *

p = Page("Request lifecycle", autonum=True)
p.add(Chips([("hot path", "amarelo"), ("async", "azul")]))

a = Card("API\ngateway", "cinza")
b = Card("auth check", "amarelo", critical=True)
c = Card("worker pool", "azul", meta="scales horizontally")
p.add(Lane("The core flow", HStack(a, b, c)))

p.arrow(a, c, label="on cache hit")
p.note(b, 1, "fails closed", palette="vermelho")
p.save("/tmp/lifecycle.excalidraw")   # validates everything, prints the tree
```

## How it works

- **Two layers, ~1,500 lines of dependency-free Python.** `excalidraw_dom.py` is the declarative layout layer; `excalidraw_engine.py` is the emission backend that writes Excalidraw JSON primitives (cards, freedraw pencil strokes, tables, sketch axes). The layout layer never touches JSON; the backend never makes layout decisions.

- **A strict phase pipeline**: freeze (stable uids per tree path) → reserve (brackets and attachments add breathing room to their target container) → measure (bottom-up, pure, memoized) → arrange (top-down, each frame written once) → overlays → validate → emit. A guard comment at the top of the source forbids parent-imposed widths (stretch, %, container-driven wrap); keeping `measure()` a pure function of the subtree is what makes layout a two-pass O(n) computation with no reflow fixpoint.

- **A centerline protocol, analogous to text baseline.** Every node exposes `line()`, the offset from its own top to its alignment axis. `HStack` aligns children by centerline; `VStack` delegates its line to the first solid child. An icon stacked above a card, or a caption below it, never pulls the card off the lane rail, by construction. Rails are an output of layout, never an input.

- **References are Python objects, never string ids.** `p.arrow(a, c)` resolves after layout, routes as pure vertical/horizontal or a single elbow (a diagonal is an error, and an elbow under 16px is too), and collision-checks the route against inflated bounding boxes of every tangible node.

- **Violations come back aggregated.** `save()` collects every layout problem into one `LayoutError` with suggested fixes, so the model corrects the whole batch in a single regenerate cycle. `debug=True` draws ghost frames for visual inspection; `strict=False` saves anyway so a broken layout can be examined.

- **Taste is encoded as defaults.** `references/design-rules.md` holds 12 layout rules extracted by diffing generated diagrams against the owner's manual corrections (shrink-to-fit cards in fixed slots, rails that start and end at card edges, cross-hatch icon fills, monospace everywhere for deterministic text measurement). Rules that can be mechanized become container defaults or validations, each backed by a regression test, so the engine converges on the owner's taste over time.

## Usage

Install as a Claude Code skill:

```bash
git clone https://github.com/MrIago/excalidraw-diagram.git ~/.claude/skills/excalidraw-diagram
```

Then ask for a diagram in plain language ("diagram the auth flow", "timeline of the migration", "compare approach A and B"). Claude plans the visual argument, writes a throwaway generator script, runs it, and points you at the saved file. View it with the Excalidraw extension in VS Code or Obsidian, or on excalidraw.com.

Node vocabulary: `Card`, `Conclusion`, `Zone`, `Text`, `Icon` (11 built-in pencil glyphs), `Chips`, `Table`, `Graph` (plots a Python lambda as a hand-drawn curve with sketch axes), `Sketch` (escape hatch for custom pencil work), `Spacer`. Containers: `Lane`, `HStack`, `VStack`, `Grid`. Seven semantic palettes where color encodes meaning (state, authorship), never decoration.

Engine maintenance runs two gates before a change lands: `scripts/test_excalidraw_dom.py` (a regression suite built from an adversarial attack on the layout rules) and the module selftest (`python3 excalidraw_dom.py`). Backend changes also get a golden test: regenerate an old diagram and compare byte for byte.

## Scope and honest limits

- The output is an `.excalidraw` file, viewed in an external editor. The skill renders no images and takes no screenshots.
- The design rules encode one person's taste: hand-drawn roughness, cross-hatch fills, a fixed palette, monospace type. Fork `references/design-rules.md` to change the look; the engine defaults follow it.
- Text measurement assumes monospace (`fontFamily: 3`). That choice is what makes measurement deterministic without a renderer; proportional fonts would break it.
- Arrows route as pure vertical/horizontal or one elbow. The engine rejects diagonals on purpose, as a legibility rule, and there is no override.
- The vocabulary covers structured diagrams (lanes, stacks, grids, tables, plotted curves). Freeform illustration goes through the `Sketch` escape hatch or not at all.
- `SKILL.md` and the rule file are written in Portuguese (the owner's language); the engine API and this README are language-neutral.

## License

MIT © [Iago Lima Toledo](https://github.com/MrIago)
