# Story Designer tool workspace

The Designer's central area is organized as focused authoring tools. Each
tool owns a narrow navigator, a primary editor, and one right-side context
surface. The full project hierarchy is available in the Project tab rather
than as a permanent application dock.

## Tools

- Project: full project tree and project summary.
- Scenes: scene-only navigator, Scene Canvas, and the contextual Scene/Object/
  Look Region editor.
- Dialogue: scene-only navigator and dialogue editor.
- Scene Graph: scene-only navigator, graph canvas, and the existing Navigation
  editor.
- Battles and Combat Moves: filtered definition navigators with their existing
  mature editors.
- Assets: asset browser with its existing search, filtering, and preview.

Scene navigation editing is owned by Scene Graph. The legacy Navigation widget
is retained as a hidden compatibility object for older integrations, but it is
not placed in the Scenes layout.

## Layout persistence

The old dock topology is intentionally not restored. The layout version is
stored as `workspaceLayoutVersion=2`; each ToolShell splitter is stored under
`toolSplitter/<tool>`. Recent-story settings remain unchanged. Users can resize
the three panes independently, and the positions are restored when the
Designer restarts.

The refresh contract remains unchanged: scalar value edits update existing
widgets in place, structural edits perform controlled partial updates, and
project reloads may reconstruct the broader workspace.
