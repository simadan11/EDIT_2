import os
import subprocess
import sys

def run_skill(args, player=None):
    """Создаёт на рабочем столе ярлык для запуска платформы LUMEN
    (веб-интерфейс + API: python -m lumen serve)."""
    desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
    shortcut_path = os.path.join(desktop_path, 'Запустить LUMEN.bat')
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    launch_command = f'cd /d "{project_dir}" && python -m lumen serve --port 8090'

    try:
        with open(shortcut_path, 'w', encoding='cp866') as f:
            f.write(f"@echo off\n{launch_command}\npause\nexit")
        return ("Ярлык 'Запустить LUMEN.bat' создан на рабочем столе — "
                "он запускает платформу LUMEN (интерфейс на http://localhost:8090).")
    except Exception as e:
        return f"Не удалось создать ярлык: {e}"
