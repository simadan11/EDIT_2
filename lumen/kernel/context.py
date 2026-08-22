"""
LUMEN — сборка контекста (ContextWeaver).

Стадия 4 конвейера «Луч». Собирает «светонос» запроса — пакет данных,
который уходит на генерацию:

  1. системный промпт персоны (lumen.persona + выученные предпочтения);
  2. семантические факты (relevance-выборка, топ-K, с бюджетом);
  3. историю сессии (последние N реплик, обрезка по бюджету);
  4. результаты инструментов (текстовым блоком — бэкенд не обязан
     уметь function-calling, конвейер сам их исполняет).

Бюджет токенов — грубая оценка (4 знака ≈ 1 токен): контекст никогда
не выходит за token_budget, стареющие реплики отбрасываются первыми.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..persona import build_system_prompt


class ContextWeaver:
    def __init__(self, config, memory) -> None:
        self.config = config
        self.memory = memory

    # ── оценка объёма ────────────────────────────────────────────────────────
    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text or "") // 4)

    def weave(self, session_id: Optional[str] = None,
              tool_results: Optional[List[Dict[str, Any]]] = None,
              learned_prefs: Optional[List[str]] = None) -> Dict[str, Any]:
        max_turns = int(self.config.get("context.max_turns", 12))
        max_facts = int(self.config.get("context.max_facts", 6))
        budget = int(self.config.get("context.token_budget", 6000))

        last_user = self.memory.last_user_text(session_id)
        facts = self.memory.recall_lines(last_user, top_k=max_facts)

        system = build_system_prompt(
            name=self.config.get("persona.name", "LUMEN"),
            style=self.config.get("persona.style", "balanced"),
            learned_prefs=learned_prefs or [],
        )
        if facts:
            system += "\n[FACTS] (из долговременной памяти, актуальны, если не противоречат разговору):\n"
            system += "\n".join(f"• {f}" for f in facts)

        # история с отсечением последней реплики (она передаётся отдельно)
        history = self.memory.history(session_id, max_turns=max_turns * 2)
        # отсекаем самый свежий user-месседж — он придёт в messages
        if history and history[-1]["role"] == "user" and last_user \
                and history[-1]["content"] == last_user:
            history = history[:-1]

        tools_block = ""
        if tool_results:
            lines = []
            for tr in tool_results:
                status = "ok" if tr.get("ok") else "error"
                text = (tr.get("text") or tr.get("error") or "").strip()[:1500]
                lines.append(f"[{status}] {tr.get('name')}: {text}")
            tools_block = "[TOOLS] (результаты инструментов по вашему запросу):\n" + "\n".join(lines)

        # бюджет: отбрасываем старые реплики
        used = self._tokens(system) + self._tokens(tools_block) + self._tokens(last_user)
        msgs: List[Dict[str, str]] = []
        for m in reversed(history):
            cost = self._tokens(m["content"]) + 4
            if used + cost > budget:
                break
            msgs.insert(0, {"role": "assistant" if m["role"] == "lumen" else "user",
                            "content": m["content"][:2000]})
            used += cost
        msgs.reverse()

        return {
            "system": system,
            "messages": msgs,
            "last_user": last_user,
            "tools_block": tools_block,
            "facts": facts,
            "token_estimate": used,
        }

    def final_messages(self, ctx: Dict[str, Any]) -> List[Dict[str, str]]:
        """Итоговый список сообщений для бэкенда."""
        msgs = list(ctx.get("messages", []))
        user_content = ctx.get("last_user", "")
        if ctx.get("tools_block"):
            user_content = ctx["tools_block"] + "\n\n" + user_content
        msgs.append({"role": "user", "content": user_content})
        return msgs
