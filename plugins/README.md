# EDIT Plugins

Drop a folder in here (or use **PLUGIN MANAGER → INSTALL**) and the assistant can
use it as a tool immediately.

```
plugins/
  my_plugin/
    plugin.json     ← manifest (name, description, tools…)
    plugin.py       ← code
```

`plugin.json`:

```json
{
  "name": "my_plugin",
  "title": "My Plugin",
  "version": "1.0.0",
  "author": "you",
  "description": "What the plugin does",
  "entry": "plugin.py",
  "tools": [
    {
      "name": "my_tool",
      "description": "What the tool does",
      "parameters": {
        "type": "object",
        "properties": { "query": { "type": "string", "description": "…" } },
        "required": ["query"]
      }
    }
  ]
}
```

`plugin.py` exposes one of:

```python
def run_tool(tool_name: str, args: dict, player=None) -> str:
    return "result"

# or, for a single-tool plugin:
def run(args: dict, player=None) -> str:
    return "result"
```

`player` is the UI object (`write_log`, `show_content`, `set_state`…), may be
`None`. Always return a short human-readable string — it goes back to the AI
and to the log.

Enable / disable everything from the **🧩 PLUGIN MANAGER** button in
SETTINGS & CONTROLS. State lives in `config/plugins.json`.
